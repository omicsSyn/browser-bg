# Content-free readiness and CDP diagnostics for the attached page only.
$script:observedSession = $null
$script:recentPageEvents = @()
$script:pendingPageRequests = @{}
$script:pageCounters = @{navigations=0; requests=0; httpErrors=0; networkFailures=0; generationRequests=0}

function AddPageEvent($entry) {
    $entry.observedAt = [DateTime]::UtcNow.ToString('o')
    $script:recentPageEvents = @(@($script:recentPageEvents) + @($entry) | Select-Object -Last 40)
}

function RecordPageEvent($event) {
    if (-not $script:observedSession -or $event.sessionId -ne $script:observedSession) { return }
    $p = $event.params
    switch ($event.method) {
        'Page.frameNavigated' {
            if (-not $p.frame.parentId) {
                $script:pageCounters.navigations++
                AddPageEvent @{kind='navigation'; origin=(SafePageUrl $p.frame.url)}
            }
        }
        'Network.requestWillBeSent' {
            if ($p.type -notin @('Fetch','XHR','Document','Script')) { return }
            $script:pageCounters.requests++
            $kind = 'other'
            $uri = $null
            if ([Uri]::TryCreate([string]$p.request.url, [UriKind]::Absolute, [ref]$uri) -and
                $uri.Host -eq 'gemini.google.com' -and $uri.AbsolutePath -match '/StreamGenerate$') {
                $kind = 'generation'
                $script:pageCounters.generationRequests++
            }
            # Never retain URL paths, queries, bodies, headers, cookies or request text.
            if ($script:pendingPageRequests.Count -ge 256) {
                $oldest = @($script:pendingPageRequests.GetEnumerator() | Sort-Object {$_.Value.observedAt})[0].Key
                $script:pendingPageRequests.Remove($oldest)
            }
            $script:pendingPageRequests[$p.requestId] = @{
                origin=(SafePageUrl $p.request.url); type=$p.type; endpoint=$kind
                observedAt=[DateTime]::UtcNow.ToString('o')
            }
            if ($kind -eq 'generation') { AddPageEvent @{kind='request-start'; endpoint=$kind; origin=(SafePageUrl $p.request.url)} }
        }
        'Network.responseReceived' {
            $request = $script:pendingPageRequests[$p.requestId]
            if ($null -eq $request) { return }
            if ($p.response.status -ge 400) { $script:pageCounters.httpErrors++ }
            if ($request.endpoint -eq 'generation' -or $p.response.status -ge 400) {
                AddPageEvent @{kind='response'; origin=$request.origin; endpoint=$request.endpoint; status=$p.response.status}
            }
        }
        'Network.loadingFailed' {
            $request = $script:pendingPageRequests[$p.requestId]
            if ($null -eq $request) { return }
            $script:pageCounters.networkFailures++
            $code = if ($p.errorText -match '^net::ERR_[A-Z0-9_]+$') { $p.errorText } else { 'network failure' }
            AddPageEvent @{kind='request-failed'; origin=$request.origin; endpoint=$request.endpoint; code=$code; canceled=[bool]$p.canceled}
            $script:pendingPageRequests.Remove($p.requestId)
        }
        'Network.loadingFinished' {
            $request = $script:pendingPageRequests[$p.requestId]
            if ($null -eq $request) { return }
            if ($request.endpoint -eq 'generation') {
                AddPageEvent @{kind='request-finished'; origin=$request.origin; endpoint=$request.endpoint}
            }
            $script:pendingPageRequests.Remove($p.requestId)
        }
    }
}

function PageDiagnostics {
    return @{counters=$script:pageCounters; recentEvents=@($script:recentPageEvents); pendingRequests=$script:pendingPageRequests.Count}
}

function GetPageState($sessionId, [string]$expectedOrigin) {
    $expression = @'
(()=>{const gemini=location.origin==='https://gemini.google.com';
const editor=document.querySelector('[contenteditable="true"][role="textbox"]');
const mode=document.querySelector('[data-test-id="bard-mode-menu-button"]');
const usable=e=>{if(!e||e.disabled||e.getAttribute('aria-disabled')==='true'||e.closest('[inert]'))return false;
const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&s.visibility!=='collapse';};
return {origin:location.origin,readyState:document.readyState,width:innerWidth,height:innerHeight,
documentStartedAt:performance.timeOrigin,navigationType:performance.getEntriesByType('navigation')[0]?.type||null,
editorPresent:gemini?usable(editor):null,modePickerPresent:gemini?usable(mode):null,
queryCount:gemini?document.querySelectorAll('user-query').length:null,
replyCount:gemini?document.querySelectorAll('model-response').length:null,
generating:gemini?[...document.querySelectorAll('button')].some(e=>/Stop response|Stop generating|停止生成|停止回答/i.test(e.getAttribute('aria-label')||'')):null};})()
'@
    $result = CDP 'Runtime.evaluate' @{expression=$expression; returnByValue=$true; timeout=3000} $sessionId
    if ($result.exceptionDetails -or -not $result.result.value) { throw 'Page readiness could not be read' }
    $state = $result.result.value
    $reason = if ($state.origin -ne $expectedOrigin) { 'origin-mismatch' }
        elseif ($state.width -le 0 -or $state.height -le 0) { 'zero-viewport' }
        elseif ($state.readyState -notin @('interactive','complete')) { 'document-loading' }
        elseif ($expectedOrigin -eq 'https://gemini.google.com' -and (-not $state.editorPresent -or -not $state.modePickerPresent)) { 'gemini-controls-missing' }
        else { $null }
    $state | Add-Member NoteProperty interactionReady ($null -eq $reason)
    $state | Add-Member NoteProperty reason $reason
    return $state
}

function ReadPageState($sessionId, [string]$expectedOrigin) {
    try { return (GetPageState $sessionId $expectedOrigin) }
    catch {
        # These completed CDP errors can occur during navigation; transport timeouts
        # and all unrecognized errors still unwind the connection.
        if ($_.Exception.Message -match 'Execution context was destroyed|Cannot find context with specified id|Cannot find default execution context') {
            return [pscustomobject]@{interactionReady=$false; reason='page-transition'; transient=$true}
        }
        throw
    }
}

function WaitPageReady($sessionId, [string]$expectedOrigin) {
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    do {
        $script:lastPageState = ReadPageState $sessionId $expectedOrigin
        if ($script:lastPageState.interactionReady) { return $script:lastPageState }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ('PAGE_NOT_INTERACTIVE: page failed readiness check (' + $script:lastPageState.reason +
        '). No content submitted. Use an initialized authorized existing tab with serve; no foreground fallback was attempted.')
}

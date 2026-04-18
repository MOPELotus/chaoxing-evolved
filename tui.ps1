$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:Title = "超星助手 TUI"
$script:Tabs = @("概览", "档案", "日志", "全局")
$script:State = @{
    TabIndex = 0
    Profiles = @()
    SelectedIndex = 0
    Global = $null
    Message = "就绪"
    LastRefresh = Get-Date
}

function Get-PythonCommand {
    $candidates = @(
        (Join-Path $script:RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path $script:RepoRoot ".venv\bin\python"),
        "python"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -eq "python") {
            $command = Get-Command python -ErrorAction SilentlyContinue
            if ($command) {
                return $command.Source
            }
            continue
        }

        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "未找到可用的 Python 解释器。"
}

$script:Python = Get-PythonCommand

function Invoke-Backend {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter()]
        $InputObject
    )

    $command = @("-m", "tui.backend") + $Arguments
    if ($PSBoundParameters.ContainsKey("InputObject")) {
        $json = $InputObject | ConvertTo-Json -Depth 100
        $output = $json | & $script:Python @command
    } else {
        $output = & $script:Python @command
    }

    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        if ($exitCode -ne 0) {
            throw "后端命令执行失败：$($Arguments -join ' ')"
        }
        return $null
    }

    $parsed = $text | ConvertFrom-Json -AsHashtable -Depth 100
    if ($exitCode -ne 0) {
        $message = if ($parsed.ContainsKey("error")) { [string]$parsed.error } else { "后端命令执行失败。" }
        throw $message
    }
    return $parsed
}

function Set-Message {
    param([string]$Message)
    $script:State.Message = $Message
}

function Get-SelectedProfileName {
    if (-not $script:State.Profiles -or $script:State.Profiles.Count -eq 0) {
        return ""
    }

    if ($script:State.SelectedIndex -lt 0) {
        $script:State.SelectedIndex = 0
    }
    if ($script:State.SelectedIndex -ge $script:State.Profiles.Count) {
        $script:State.SelectedIndex = $script:State.Profiles.Count - 1
    }
    return [string]$script:State.Profiles[$script:State.SelectedIndex].name
}

function Refresh-Data {
    $profiles = @(Invoke-Backend -Arguments @("list-profiles"))
    $script:State.Profiles = $profiles
    if ($profiles.Count -eq 0) {
        $script:State.SelectedIndex = 0
    } elseif ($script:State.SelectedIndex -ge $profiles.Count) {
        $script:State.SelectedIndex = $profiles.Count - 1
    }
    $script:State.Global = Invoke-Backend -Arguments @("get-global")
    $script:State.LastRefresh = Get-Date
}

function Get-ProfileView {
    $name = Get-SelectedProfileName
    if ([string]::IsNullOrWhiteSpace($name)) {
        return $null
    }
    return Invoke-Backend -Arguments @("profile-view", "--name", $name)
}

function Get-LogView {
    $name = Get-SelectedProfileName
    if ([string]::IsNullOrWhiteSpace($name)) {
        return @{ profile_name = ""; lines = @(); status = "idle"; path = "" }
    }
    return Invoke-Backend -Arguments @("read-log", "--name", $name, "--lines", "28")
}

function Get-StatusLabel {
    param($Run)
    if (-not $Run) {
        return "未启动"
    }

    switch ([string]$Run.status) {
        "running" { return "运行中" }
        "completed" { return "已完成" }
        "failed" { return "失败" }
        "stopped" { return "已停止" }
        "stopping" { return "停止中" }
        default { return "未启动" }
    }
}

function Fit-Text {
    param(
        [string]$Text,
        [int]$Width
    )

    if ($Width -le 0) {
        return ""
    }

    $value = if ($null -eq $Text) { "" } else { [string]$Text }
    if ($value.Length -gt $Width) {
        if ($Width -eq 1) {
            return "…"
        }
        return $value.Substring(0, $Width - 1) + "…"
    }
    return $value.PadRight($Width)
}

function New-BoxLines {
    param(
        [string]$Title,
        [string[]]$Lines,
        [int]$Width,
        [int]$Height
    )

    $innerWidth = [Math]::Max($Width - 2, 1)
    $visibleLines = @($Lines)
    $result = New-Object System.Collections.Generic.List[string]
    $titleText = if ([string]::IsNullOrWhiteSpace($Title)) { "" } else { " $Title " }
    if ($titleText.Length -gt $innerWidth) {
        $titleText = $titleText.Substring(0, $innerWidth)
    }
    $topContent = $titleText.PadRight($innerWidth, [char]"─")
    $result.Add("┌" + $topContent + "┐")

    foreach ($line in $visibleLines | Select-Object -First ([Math]::Max($Height - 2, 0))) {
        $result.Add("│" + (Fit-Text -Text $line -Width $innerWidth) + "│")
    }

    while ($result.Count -lt ($Height - 1)) {
        $result.Add("│" + ("".PadRight($innerWidth)) + "│")
    }

    $result.Add("└" + ("".PadRight($innerWidth, [char]"─")) + "┘")
    return [string[]]$result.ToArray()
}

function Format-DateTime {
    param($Value)
    if (-not $Value) {
        return "-"
    }
    return [DateTimeOffset]::FromUnixTimeSeconds([int][Math]::Floor([double]$Value)).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")
}

function Get-OverviewLines {
    $profiles = $script:State.Profiles
    $running = @($profiles | Where-Object { $_.run -and $_.run.status -eq "running" }).Count
    $failed = @($profiles | Where-Object { $_.run -and ($_.run.status -eq "failed" -or $_.run.status -eq "stopped") }).Count
    $selected = Get-ProfileView
    $lines = @(
        "配置总数：$($profiles.Count)",
        "运行中：$running",
        "需关注：$failed",
        "",
        "当前说明：",
        "本界面使用 PowerShell 全屏终端交互。",
        "左侧维护配置列表，右侧显示当前页面详情。",
        ""
    )

    if ($selected) {
        $summary = $selected.summary
        $lines += @(
            "当前档案：$($selected.profile.name)",
            "运行状态：$(Get-StatusLabel -Run $selected.run)",
            "题库提供方：$($summary.provider)",
            "协同题库：$(([string[]]$summary.providers) -join ', ')",
            "课程数量：$($summary.course_count)",
            "账号：$($summary.username)",
            "Cookies 登录：$($summary.use_cookies)"
        )
    } else {
        $lines += "当前暂无可用档案。"
    }

    return $lines
}

function Get-ProfileLines {
    $view = Get-ProfileView
    if (-not $view) {
        return @("暂无档案。按 N 创建新配置。")
    }

    $profile = $view.profile
    $effective = $view.effective_profile
    $summary = $view.summary
    return @(
        "名称：$($profile.name)",
        "运行状态：$(Get-StatusLabel -Run $view.run)",
        "账号：$($profile.common.username)",
        "Cookies 登录：$($profile.common.use_cookies)",
        "倍速：$($profile.common.speed)",
        "并发章节数：$($profile.common.jobs)",
        "关闭章节处理策略：$($profile.common.notopen_action)",
        "",
        "题库提供方：$($profile.tiku.provider)",
        "协同题库：$(([string[]]$profile.tiku.providers) -join ', ')",
        "仲裁题库：$($profile.tiku.decision_provider)",
        "最低覆盖率：$($profile.tiku.cover_rate)",
        "单题间隔：$($profile.tiku.delay)",
        "",
        "全局生效题库：$($summary.provider)",
        "全局课程数：$($effective.common.course_list.Count)"
    )
}

function Get-LogLines {
    $logView = Get-LogView
    $lines = @(
        "档案：$($logView.profile_name)",
        "状态：$($logView.status)",
        "日志文件：$($logView.path)",
        ""
    )
    if ($logView.lines.Count -eq 0) {
        $lines += "暂无日志。"
    } else {
        $lines += [string[]]$logView.lines
    }
    return $lines
}

function Get-GlobalLines {
    $settings = $script:State.Global
    if (-not $settings) {
        return @("全局设置尚未载入。")
    }

    $tiku = $settings.defaults.tiku
    $notice = $settings.defaults.notification
    $desktop = $settings.desktop
    return @(
        "默认 AI 接口：$($tiku.endpoint)",
        "默认 AI 模型：$($tiku.model)",
        "默认硅基模型：$($tiku.siliconflow_model)",
        "默认请求超时：$($tiku.request_timeout_seconds)",
        "默认 OneBot 地址：$($notice.onebot_host):$($notice.onebot_port)$($notice.onebot_path)",
        "默认通知目标：$($notice.onebot_target_type)",
        "默认 QQ：$($notice.onebot_user_id)",
        "默认群号：$($notice.onebot_group_id)",
        "",
        "系统通知：$($desktop.system_notifications)",
        "应用内提示：$($desktop.in_app_notifications)",
        "成功提醒：$($desktop.notify_on_completed)",
        "失败提醒：$($desktop.notify_on_failed)",
        "停止提醒：$($desktop.notify_on_stopped)"
    )
}

function Get-RightPaneLines {
    switch ($script:Tabs[$script:State.TabIndex]) {
        "概览" { return Get-OverviewLines }
        "档案" { return Get-ProfileLines }
        "日志" { return Get-LogLines }
        "全局" { return Get-GlobalLines }
        default { return @() }
    }
}

function Render-Tui {
    Clear-Host

    $width = [Math]::Max([Console]::WindowWidth, 100)
    $height = [Math]::Max([Console]::WindowHeight, 32)
    $leftWidth = [Math]::Min(40, [Math]::Max([int]($width * 0.34), 28))
    $rightWidth = [Math]::Max($width - $leftWidth - 3, 40)
    $panelHeight = [Math]::Max($height - 8, 18)

    $tabLine = ($script:Tabs | ForEach-Object {
        if ($_ -eq $script:Tabs[$script:State.TabIndex]) { "【$_】" } else { " $_ " }
    }) -join "  "
    Write-Host (Fit-Text -Text $script:Title -Width ($width - 1))
    Write-Host (Fit-Text -Text $tabLine -Width ($width - 1))
    Write-Host (Fit-Text -Text ("刷新时间：{0}    当前消息：{1}" -f $script:State.LastRefresh.ToString("yyyy-MM-dd HH:mm:ss"), $script:State.Message) -Width ($width - 1))
    Write-Host ("".PadRight([Math]::Max($width - 1, 1), "─"))

    $profileLines = New-Object System.Collections.Generic.List[string]
    if ($script:State.Profiles.Count -eq 0) {
        $profileLines.Add("暂无配置。按 N 创建。")
    } else {
        for ($index = 0; $index -lt $script:State.Profiles.Count; $index++) {
            $profile = $script:State.Profiles[$index]
            $selected = if ($index -eq $script:State.SelectedIndex) { ">" } else { " " }
            $status = Get-StatusLabel -Run $profile.run
            $provider = [string]$profile.summary.provider
            $profileLines.Add(("{0} {1} [{2}] {3}" -f $selected, $profile.name, $status, $provider))
        }
    }

    $leftBox = New-BoxLines -Title "配置列表" -Lines $profileLines.ToArray() -Width $leftWidth -Height $panelHeight
    $rightBox = New-BoxLines -Title $script:Tabs[$script:State.TabIndex] -Lines (Get-RightPaneLines) -Width $rightWidth -Height $panelHeight

    for ($i = 0; $i -lt $panelHeight; $i++) {
        Write-Host ($leftBox[$i] + " " + $rightBox[$i])
    }

    Write-Host ("".PadRight([Math]::Max($width - 1, 1), "─"))
    Write-Host "热键：Tab 切页  ↑↓ 选中  R 刷新  N 新建  D 删除  E 编辑档案  G 编辑全局  S 启动  X 停止  Q 退出"
}

function Prompt-Text {
    param(
        [string]$Label,
        [string]$Default = ""
    )
    Write-Host ""
    $suffix = if ([string]::IsNullOrEmpty($Default)) { "" } else { " [$Default]" }
    $value = Read-Host "$Label$suffix"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Prompt-Confirm {
    param([string]$Label)
    $value = (Read-Host "$Label (y/N)").Trim().ToLowerInvariant()
    return $value -in @("y", "yes", "1")
}

function Save-ProfileObject {
    param($Profile)
    Invoke-Backend -Arguments @("save-profile") -InputObject $Profile | Out-Null
}

function Save-GlobalObject {
    param($Settings)
    Invoke-Backend -Arguments @("save-global") -InputObject $Settings | Out-Null
}

function Select-Courses {
    $view = Get-ProfileView
    if (-not $view) {
        return
    }
    $name = $view.profile.name
    $courses = @(Invoke-Backend -Arguments @("fetch-courses", "--name", $name))
    Clear-Host
    Write-Host "课程选择：$name"
    Write-Host ""
    for ($i = 0; $i -lt $courses.Count; $i++) {
        $mark = if ($courses[$i].selected) { "[x]" } else { "[ ]" }
        Write-Host ("{0,3}. {1} {2} ({3})" -f ($i + 1), $mark, $courses[$i].title, $courses[$i].teacher)
    }
    Write-Host ""
    $raw = Read-Host "输入要保留的课程编号，使用英文逗号分隔；留空表示清空"
    $selectedIds = @()
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        foreach ($item in ($raw -split ",")) {
            $index = 0
            if ([int]::TryParse($item.Trim(), [ref]$index) -and $index -ge 1 -and $index -le $courses.Count) {
                $selectedIds += [string]$courses[$index - 1].courseId
            }
        }
    }
    $profile = $view.profile
    $profile.common.course_list = @($selectedIds | Select-Object -Unique)
    Save-ProfileObject -Profile $profile
    Set-Message "课程列表已更新。"
}

function Edit-SelectedProfile {
    $name = Get-SelectedProfileName
    if ([string]::IsNullOrWhiteSpace($name)) {
        Set-Message "当前没有可编辑的档案。"
        return
    }

    while ($true) {
        $view = Get-ProfileView
        $profile = $view.profile
        Clear-Host
        Write-Host "编辑档案：$name"
        Write-Host "1. 账号：$($profile.common.username)"
        Write-Host "2. 密码：$(if ($profile.common.password) { '已填写' } else { '未填写' })"
        Write-Host "3. Cookies 登录：$($profile.common.use_cookies)"
        Write-Host "4. 倍速：$($profile.common.speed)"
        Write-Host "5. 并发章节数：$($profile.common.jobs)"
        Write-Host "6. 关闭章节处理策略：$($profile.common.notopen_action)"
        Write-Host "7. 题库提供方：$($profile.tiku.provider)"
        Write-Host "8. 协同题库：$(([string[]]$profile.tiku.providers) -join ', ')"
        Write-Host "9. 仲裁题库：$($profile.tiku.decision_provider)"
        Write-Host "10. 最低覆盖率：$($profile.tiku.cover_rate)"
        Write-Host "11. 单题间隔：$($profile.tiku.delay)"
        Write-Host "12. 课程选择"
        Write-Host "0. 返回"
        $choice = Read-Host "请选择要修改的项目"
        switch ($choice) {
            "1" { $profile.common.username = Prompt-Text -Label "输入账号" -Default ([string]$profile.common.username); Save-ProfileObject $profile }
            "2" { $profile.common.password = Prompt-Text -Label "输入密码" -Default ([string]$profile.common.password); Save-ProfileObject $profile }
            "3" { $profile.common.use_cookies = Prompt-Confirm -Label "是否启用 Cookies 登录"; Save-ProfileObject $profile }
            "4" { $profile.common.speed = [double](Prompt-Text -Label "输入倍速" -Default ([string]$profile.common.speed)); Save-ProfileObject $profile }
            "5" { $profile.common.jobs = [int](Prompt-Text -Label "输入并发章节数" -Default ([string]$profile.common.jobs)); Save-ProfileObject $profile }
            "6" { $profile.common.notopen_action = Prompt-Text -Label "输入策略（retry/continue/ask）" -Default ([string]$profile.common.notopen_action); Save-ProfileObject $profile }
            "7" { $profile.tiku.provider = Prompt-Text -Label "输入题库提供方" -Default ([string]$profile.tiku.provider); Save-ProfileObject $profile }
            "8" {
                $raw = Prompt-Text -Label "输入协同题库，使用英文逗号分隔" -Default (([string[]]$profile.tiku.providers) -join ",")
                $profile.tiku.providers = @($raw -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
                Save-ProfileObject $profile
            }
            "9" { $profile.tiku.decision_provider = Prompt-Text -Label "输入仲裁题库" -Default ([string]$profile.tiku.decision_provider); Save-ProfileObject $profile }
            "10" { $profile.tiku.cover_rate = [double](Prompt-Text -Label "输入覆盖率（0-1）" -Default ([string]$profile.tiku.cover_rate)); Save-ProfileObject $profile }
            "11" { $profile.tiku.delay = [double](Prompt-Text -Label "输入单题间隔（秒）" -Default ([string]$profile.tiku.delay)); Save-ProfileObject $profile }
            "12" { Select-Courses }
            "0" { Set-Message "档案修改已完成。"; return }
            default { }
        }
    }
}

function Edit-GlobalSettings {
    while ($true) {
        $settings = Invoke-Backend -Arguments @("get-global")
        Clear-Host
        Write-Host "编辑全局设置"
        Write-Host "1. 默认令牌列表：$($settings.defaults.tiku.tokens)"
        Write-Host "2. 默认 AI 接口：$($settings.defaults.tiku.endpoint)"
        Write-Host "3. 默认 AI 密钥：$(if ($settings.defaults.tiku.key) { '已填写' } else { '未填写' })"
        Write-Host "4. 默认 AI 模型：$($settings.defaults.tiku.model)"
        Write-Host "5. 默认硅基密钥：$(if ($settings.defaults.tiku.siliconflow_key) { '已填写' } else { '未填写' })"
        Write-Host "6. 默认硅基模型：$($settings.defaults.tiku.siliconflow_model)"
        Write-Host "7. 默认通知提供方：$($settings.defaults.notification.provider)"
        Write-Host "8. 默认 OneBot 地址：$($settings.defaults.notification.onebot_host)"
        Write-Host "9. 默认 OneBot 端口：$($settings.defaults.notification.onebot_port)"
        Write-Host "10. 默认 QQ：$($settings.defaults.notification.onebot_user_id)"
        Write-Host "11. 默认群号：$($settings.defaults.notification.onebot_group_id)"
        Write-Host "12. 系统通知：$($settings.desktop.system_notifications)"
        Write-Host "13. 成功提醒：$($settings.desktop.notify_on_completed)"
        Write-Host "0. 返回"
        $choice = Read-Host "请选择要修改的项目"
        switch ($choice) {
            "1" { $settings.defaults.tiku.tokens = Prompt-Text -Label "输入默认令牌列表" -Default ([string]$settings.defaults.tiku.tokens); Save-GlobalObject $settings }
            "2" { $settings.defaults.tiku.endpoint = Prompt-Text -Label "输入默认 AI 接口" -Default ([string]$settings.defaults.tiku.endpoint); Save-GlobalObject $settings }
            "3" { $settings.defaults.tiku.key = Prompt-Text -Label "输入默认 AI 密钥" -Default ([string]$settings.defaults.tiku.key); Save-GlobalObject $settings }
            "4" { $settings.defaults.tiku.model = Prompt-Text -Label "输入默认 AI 模型" -Default ([string]$settings.defaults.tiku.model); Save-GlobalObject $settings }
            "5" { $settings.defaults.tiku.siliconflow_key = Prompt-Text -Label "输入默认硅基密钥" -Default ([string]$settings.defaults.tiku.siliconflow_key); Save-GlobalObject $settings }
            "6" { $settings.defaults.tiku.siliconflow_model = Prompt-Text -Label "输入默认硅基模型" -Default ([string]$settings.defaults.tiku.siliconflow_model); Save-GlobalObject $settings }
            "7" { $settings.defaults.notification.provider = Prompt-Text -Label "输入默认通知提供方" -Default ([string]$settings.defaults.notification.provider); Save-GlobalObject $settings }
            "8" { $settings.defaults.notification.onebot_host = Prompt-Text -Label "输入默认 OneBot 地址" -Default ([string]$settings.defaults.notification.onebot_host); Save-GlobalObject $settings }
            "9" { $settings.defaults.notification.onebot_port = [int](Prompt-Text -Label "输入默认 OneBot 端口" -Default ([string]$settings.defaults.notification.onebot_port)); Save-GlobalObject $settings }
            "10" { $settings.defaults.notification.onebot_user_id = Prompt-Text -Label "输入默认 QQ" -Default ([string]$settings.defaults.notification.onebot_user_id); Save-GlobalObject $settings }
            "11" { $settings.defaults.notification.onebot_group_id = Prompt-Text -Label "输入默认群号" -Default ([string]$settings.defaults.notification.onebot_group_id); Save-GlobalObject $settings }
            "12" { $settings.desktop.system_notifications = Prompt-Confirm -Label "是否启用系统通知"; Save-GlobalObject $settings }
            "13" { $settings.desktop.notify_on_completed = Prompt-Confirm -Label "是否在成功时提醒"; Save-GlobalObject $settings }
            "0" { Set-Message "全局设置修改已完成。"; return }
            default { }
        }
    }
}

function Create-Profile {
    $name = Prompt-Text -Label "输入新档案名称"
    if ([string]::IsNullOrWhiteSpace($name)) {
        Set-Message "已取消创建。"
        return
    }
    Invoke-Backend -Arguments @("create-profile", "--name", $name) | Out-Null
    Set-Message "已创建档案：$name"
    Refresh-Data
    for ($i = 0; $i -lt $script:State.Profiles.Count; $i++) {
        if ($script:State.Profiles[$i].name -eq $name) {
            $script:State.SelectedIndex = $i
            break
        }
    }
}

function Delete-SelectedProfile {
    $name = Get-SelectedProfileName
    if ([string]::IsNullOrWhiteSpace($name)) {
        Set-Message "当前没有可删除的档案。"
        return
    }
    if (-not (Prompt-Confirm -Label "确认删除档案 $name")) {
        Set-Message "已取消删除。"
        return
    }
    Invoke-Backend -Arguments @("delete-profile", "--name", $name, "--force") | Out-Null
    Set-Message "已删除档案：$name"
    Refresh-Data
}

function Start-SelectedProfile {
    $name = Get-SelectedProfileName
    if ([string]::IsNullOrWhiteSpace($name)) {
        Set-Message "当前没有可启动的档案。"
        return
    }
    Invoke-Backend -Arguments @("start-run", "--name", $name) | Out-Null
    Set-Message "已启动档案：$name"
    Refresh-Data
}

function Stop-SelectedProfile {
    $name = Get-SelectedProfileName
    if ([string]::IsNullOrWhiteSpace($name)) {
        Set-Message "当前没有可停止的档案。"
        return
    }
    Invoke-Backend -Arguments @("stop-run", "--name", $name) | Out-Null
    Set-Message "已停止档案：$name"
    Refresh-Data
}

function Handle-Key {
    param($KeyInfo)
    $character = [string]$KeyInfo.Character
    switch ($KeyInfo.VirtualKeyCode) {
        9 {
            $script:State.TabIndex = ($script:State.TabIndex + 1) % $script:Tabs.Count
            return $true
        }
        38 {
            if ($script:State.SelectedIndex -gt 0) {
                $script:State.SelectedIndex--
            }
            return $true
        }
        40 {
            if ($script:State.SelectedIndex -lt ($script:State.Profiles.Count - 1)) {
                $script:State.SelectedIndex++
            }
            return $true
        }
    }

    switch ($character.ToLowerInvariant()) {
        "q" { return $false }
        "r" { Refresh-Data; Set-Message "数据已刷新。"; return $true }
        "n" { Create-Profile; return $true }
        "d" { Delete-SelectedProfile; return $true }
        "e" { Edit-SelectedProfile; Refresh-Data; return $true }
        "g" { Edit-GlobalSettings; Refresh-Data; return $true }
        "s" { Start-SelectedProfile; return $true }
        "x" { Stop-SelectedProfile; return $true }
        default { return $true }
    }
}

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    if ($env:CHAOXING_TUI_SELFTEST -eq "1") {
        Refresh-Data
        Write-Output "SELFTEST OK"
        exit 0
    }
    Refresh-Data
    while ($true) {
        Render-Tui
        $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
        if (-not (Handle-Key -KeyInfo $key)) {
            break
        }
    }
}
finally {
    if ($env:CHAOXING_TUI_SELFTEST -ne "1") {
        try {
            Clear-Host
        }
        catch {
        }
    }
}

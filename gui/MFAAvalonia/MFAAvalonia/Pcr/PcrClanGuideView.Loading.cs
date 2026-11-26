using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed partial class PcrClanGuideView
{
    private sealed class DisplayData : IDisposable
    {
        internal PcrClanGuideDocument Document = new();
        internal PcrGuideCatalog? Catalog;
        internal JObject? Bundle, Snapshot;
        internal Choice[] Accounts = Array.Empty<Choice>();
        internal string AccountNote = "", StateStamp = "";
        internal PcrClanCalibration Calibration = new(0, new System.Collections.Generic.HashSet<string>(), "请先选用有效账号资料");
        internal bool Confirmed;
        internal Bitmap? Image, Preview;
        internal PcrClanDailyExecutionDocument? Execution;
        internal string? ExecutionStamp;
        public void Dispose() { Image?.Dispose(); Preview?.Dispose(); }
    }
    private int loadVersion, imageVersion;
    private bool loading;
    private Task pendingLoad = Task.CompletedTask, pendingImages = Task.CompletedTask;
    private JObject? displaySnapshot;
    private PcrClanCalibration displayCalibration = new(0, new System.Collections.Generic.HashSet<string>(), "请先选用有效账号资料");
    public Task PendingLoad => Task.WhenAll(pendingLoad, pendingImages, dailyPlan.PendingLoad);
    private static PcrClanGuideDocument Clone(PcrClanGuideDocument value) => JsonConvert.DeserializeObject<PcrClanGuideDocument>(JsonConvert.SerializeObject(value))!;
    private DisplayData PrepareDisplay(PcrClanGuideDocument value, bool showImages, PcrClanGuide.PlanInputs? inputs = null)
    {
        inputs ??= new();
        var data = new DisplayData { Document = Clone(value) };
        try
        {
            if (!string.IsNullOrEmpty(value.BundlePath))
            {
                if (inputs.Stamp(value.BundlePath) != value.BundleSha256) throw new InvalidDataException("作业原件摘要不一致。");
                data.Bundle = PcrClanGuide.ValidateBundle(inputs.Json(value.BundlePath));
            }
            try
            {
                data.Snapshot = PcrClanGuide.Snapshot(value, configDirectory, repository, DateTimeOffset.Now, inputs, out var verifiedCatalog);
                data.Catalog = verifiedCatalog;
                data.AccountNote = $"账号资料可用：{data.Snapshot["account_id"]} · 内容版本 {data.Snapshot["content_version"]} · {((bool?)data.Snapshot["ownership_complete"] == true ? "完整持有名单" : "部分持有资料")}。采集于 {data.Snapshot["observed_at"]}，有效期 24 小时。";
            }
            catch (Exception ex)
            {
                data.AccountNote = string.IsNullOrEmpty(value.SnapshotPath) ? "尚未选择账号资料。可在“账号资料与刷新”读取，再选择所需账号。"
                    : "账号资料不可用：" + ex.Message + "；请在“账号资料与刷新”核对后，点击“选用最新账号资料”。";
            }
            try
            {
                if (data.Catalog == null)
                {
                    var old = Path.Combine(configDirectory, "guide-review.json");
                    if (File.Exists(old))
                    {
                        try { data.Catalog = PcrGuideReview.Parse(System.Text.Encoding.UTF8.GetString(inputs.Read(old))).Input.Catalog; }
                        catch (Exception) { }
                    }
                    if (data.Catalog == null)
                    {
                        var path = Path.Combine(repository, "assets", "labyrinth", "character-pack", "characters.landosolroster.json");
                        var roster = inputs.Json(path);
                        if ((int?)roster["schemaVersion"] != 1 || roster["characters"] is not JArray rows) throw new InvalidDataException("原角色目录格式无效。");
                        var units = rows.OfType<JObject>().Select(row => new { Id = (string?)row["id"], Name = !string.IsNullOrWhiteSpace((string?)row["officialName"]) ? (string?)row["officialName"] : (string?)row["name"] })
                            .Where(row => int.TryParse(row.Id, out var id) && id is > 1000 and <= 9999 && !string.IsNullOrWhiteSpace(row.Name))
                            .Select(row => new PcrGuideUnit(int.Parse(row.Id!, System.Globalization.CultureInfo.InvariantCulture) * 100 + 1, row.Name!)).ToList();
                        if (units.Count == 0 || units.Select(unit => unit.UnitId).Distinct().Count() != units.Count) throw new InvalidDataException("原角色目录为空或编号重复。");
                        data.Catalog = new() { Server = "cn-bilibili", Source = path, Sha256 = inputs.Stamp(path), Units = units };
                    }
                }
            }
            catch (Exception ex) { data.Catalog = null; data.AccountNote += " 角色目录暂不可用：" + ex.Message; }
            data.Accounts = PcrClanGuide.SnapshotPaths(configDirectory).Select(path =>
            {
                try { var row = inputs.Json(path); return new Choice(path, $"账号 {row["account_id"]} · {row["observed_at"]} · {row["content_version"]}"); }
                catch (Exception) { return null; }
            }).Where(choice => choice != null).Cast<Choice>().ToArray();
            data.Calibration = PcrClanCalibration.Read(repository, (string?)data.Snapshot?["content_version"]);
            if (value.ConfirmedFor != null)
            {
                try { data.Confirmed = value.ConfirmedFor == (string?)PcrClanGuide.BuildPlan(value, configDirectory, repository, DateTimeOffset.Now, inputs)["confirmed_for"]; }
                catch (Exception) { }
            }
            if (showImages) (data.Image, data.Preview) = ReadImages(value, inputs, data.Bundle);
            inputs.Verify();
            return data;
        }
        catch { data.Dispose(); throw; }
    }
    private static (Bitmap? Image, Bitmap? Preview) ReadImages(PcrClanGuideDocument value, PcrClanGuide.PlanInputs inputs, JObject? sourceBundle)
    {
        Bitmap? original = null, crop = null;
        try
        {
            var job = value.Job; JObject? originalJob = null;
            if (!string.IsNullOrEmpty(value.BundlePath))
            {
                if (inputs.Stamp(value.BundlePath) != value.BundleSha256) throw new InvalidDataException("作业原件摘要不一致。");
                sourceBundle ??= PcrClanGuide.ValidateBundle(inputs.Json(value.BundlePath));
                originalJob = ((JArray)sourceBundle["jobs"]!).OfType<JObject>().SingleOrDefault(row => (string?)row["source"]?["job_id"] == value.SelectedJobId);
                if (originalJob == null || !JToken.DeepEquals(job["source"], originalJob["source"])) throw new InvalidDataException("图片来源与当前作业原件不符。");
            }
            if (job["source"]?["image_path"]?.Type == JTokenType.String)
            {
                var path = (string)job["source"]!["image_path"]!;
                if (inputs.Stamp(path) != (string?)job["source"]?["image_sha256"]) throw new InvalidDataException("原图摘要不一致。");
                using var stream = new MemoryStream(inputs.Read(path)); original = new Bitmap(stream);
            }
            var encoded = originalJob?["image_base64"] ?? job["image_base64"];
            if (encoded?.Type == JTokenType.String && ((string)encoded).Length is > 0 and < 8_000_000)
            { using var stream = new MemoryStream(Convert.FromBase64String((string)encoded)); crop = new Bitmap(stream); }
            return (original, crop);
        }
        catch { original?.Dispose(); crop?.Dispose(); throw; }
    }
    private void ApplyAccount(DisplayData data)
    {
        catalog = data.Catalog; displaySnapshot = data.Snapshot; displayCalibration = data.Calibration;
        var before = displaying; displaying = true;
        try { accounts.ItemsSource = data.Accounts; accounts.SelectedItem = data.Accounts.FirstOrDefault(choice => choice.Value == document.SnapshotPath); }
        finally { displaying = before; }
        accountNote.Text = data.AccountNote; UpdateCalibrationSummary();
    }
    private void SetLoading(bool value)
    {
        loading = value;
        foreach (var field in fields.Values) field.IsEnabled = !value;
        foreach (var member in members) { member.Role.IsEnabled = member.Star.IsEnabled = member.Set.IsEnabled = !value; }
        auto.IsEnabled = support.IsEnabled = supportRole.IsEnabled = switches.IsEnabled = !value;
        executionMode.IsEnabled = !value;
        battleSpeed.IsEnabled = !value && executionLoaded && execution.Mode == PcrClanDailyExecution.Simulation;
    }
    private void CancelPendingLoad() { loadVersion++; dailyPlan.CancelPendingLoad(); SetLoading(false); }
    private Task QueueDisplay(Func<DisplayData> read, Action<DisplayData> apply, string message)
    {
        dailyPlan.CancelPendingLoad();
        var version = ++loadVersion; SetLoading(true); status.Text = message;
        return pendingLoad = Complete();
        async Task Complete()
        {
            DisplayData? data = null;
            try
            {
                data = await Task.Run(read).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (version != loadVersion) return;
                    apply(data);
                    videoStatus.Text = status.Text;
                });
            }
            catch (Exception ex)
            {
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (version != loadVersion) return;
                    document.ConfirmedFor = null; execution.ConfirmedFor = null;
                    SetVideoStatus("作业不可用，尚未确认：" + ex.Message);
                });
            }
            finally
            {
                data?.Dispose();
                await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) SetLoading(false); });
            }
        }
    }
    public Task SelectJobAsync(string jobId)
    {
        var value = Clone(document); var expanded = workSelection.IsExpanded; var showImages = sourceDetails.IsExpanded;
        return QueueDisplay(() => { var inputs = new PcrClanGuide.PlanInputs(); PcrClanGuide.Select(value, value.BundlePath, jobId, inputs); return PrepareDisplay(value, showImages, inputs); }, data =>
        {
            ApplyDisplay(data); workSelection.IsExpanded = expanded; bundle = data.Bundle; SynchronizeSelection();
            if (!string.IsNullOrEmpty(cacheNotice.Text)) { document.ConfirmedFor = null; data.Confirmed = false; }
            status.Text = data.Confirmed ? "已恢复本队保存的核对，账号和原件保持一致。" : "已载入作业。未知字段请对照原图明确填写，完成后一次确认保存。";
            videoStatus.Text = status.Text;
        }, "正在载入所选队伍…");
    }
    public Task LoadBundleAsync(string path) => LoadBundleAsync(path, null);
    private Task LoadBundleAsync(string path, int? preferredPage)
    {
        var value = Clone(document); var showImages = sourceDetails.IsExpanded; string? chosenPage = null;
        return QueueDisplay(() =>
        {
            var inputs = new PcrClanGuide.PlanInputs(); var next = PcrClanGuide.ValidateBundle(inputs.Json(path));
            var pages = (JArray)next["pages"]!;
            chosenPage = preferredPage?.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? pages[0]["page"]!.ToString();
            if (!pages.Any(page => page["page"]!.ToString() == chosenPage)) throw new InvalidDataException("缓存中没有对应的分 P。");
            value.BundlePath = Path.GetFullPath(path); value.BundleSha256 = inputs.Stamp(path); value.ConfirmedFor = null;
            var first = ((JArray)next["jobs"]!).OfType<JObject>().FirstOrDefault(job => job["source"]?["page"]?.ToString() == chosenPage);
            if (first != null) PcrClanGuide.Select(value, path, (string)first["source"]!["job_id"]!, inputs);
            var data = PrepareDisplay(value, showImages && first != null, inputs); data.Bundle = next; data.Confirmed = false; return data;
        }, data =>
        {
            cacheNotice.Text = ""; bundle = data.Bundle; ApplyDisplay(data); document.ConfirmedFor = null;
            if (document.Job["source"]?["page"]?.ToString() == chosenPage) SynchronizeSelection();
            else
            {
                displaying = true;
                try { page.ItemsSource = ((JArray)bundle!["pages"]!).Select(row => new Choice(row["page"]!.ToString(), $"P{row["page"]} · {row["label"]}")).ToArray(); page.SelectedItem = ((System.Collections.Generic.IEnumerable<Choice>)page.ItemsSource!).First(choice => choice.Value == chosenPage); }
                finally { displaying = false; }
                PopulatePackages(true);
            }
            workSelection.IsExpanded = true;
            SetVideoStatus(teams.SelectedItem is Choice ? "已载入视频作业，请选择分 P、套餐和队伍并核对。" : "当前分 P 未识别到作业，请选择其他分 P。");
        }, "正在读取作业包…");
    }
    public Task ReloadAsync()
    {
        var showImages = sourceDetails.IsExpanded; loaded = false; cacheNotice.Text = "";
        return QueueDisplay(() =>
        {
            var value = PcrClanGuide.Read(statePath, out var stamp); var data = PrepareDisplay(value, showImages);
            data.StateStamp = stamp ?? ""; data.Execution = PcrClanDailyExecution.Read(PcrClanDailyExecution.PathFor(configDirectory), out data.ExecutionStamp); return data;
        }, data =>
        {
            loadedHash = data.StateStamp.Length == 0 ? null : data.StateStamp; bundle = data.Bundle; ApplyDisplay(data); SynchronizeSelection(); loaded = true;
            status.Text = data.Confirmed ? "已载入本队保存的核对。" : "已载入草稿；请选作业、核对五人和操作后一次保存。";
            execution = data.Execution!; executionHash = data.ExecutionStamp; executionLoaded = true; SetExecutionDisplay();
            _ = dailyPlan.ReloadAsync(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
        }, "正在载入公会战资料…");
    }
    private Task RestoreImagesAsync()
    {
        if (bitmap != null || previewBitmap != null || document.Job["source"]?["image_path"]?.Type != JTokenType.String) return Task.CompletedTask;
        var version = ++imageVersion; var value = Clone(document);
        return pendingImages = Complete();
        async Task Complete()
        {
            Bitmap? original = null, crop = null;
            try
            {
                (original, crop) = await Task.Run(() => { var inputs = new PcrClanGuide.PlanInputs(); var result = ReadImages(value, inputs, null); try { inputs.Verify(); return result; } catch { result.Image?.Dispose(); result.Preview?.Dispose(); throw; } }).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (version != imageVersion || !sourceDetails.IsExpanded) return;
                    bitmap = original; previewBitmap = crop; image.Source = original; preview.Source = crop; original = crop = null;
                });
            }
            catch (Exception ex)
            { await Dispatcher.UIThread.InvokeAsync(() => { if (version == imageVersion) { document.ConfirmedFor = null; status.Text = "图片恢复失败，旧确认已取消；编辑内容保留：" + ex.Message; } }); }
            finally { original?.Dispose(); crop?.Dispose(); }
        }
    }
    private Task UseSelectedAccountAsync()
    {
        if (accounts.SelectedItem is not Choice choice) { status.Text = "请先选择账号资料。"; return Task.CompletedTask; }
        var value = Clone(document); var selected = choice.Value;
        return QueueDisplay(() =>
        {
            value.SnapshotPath = selected; value.SnapshotSha256 = PcrClanGuide.Stamp(selected) ?? throw new IOException("所选账号资料不存在，请重新读取。");
            return PrepareDisplay(value, false);
        }, data =>
        {
            var changed = document.SnapshotPath != data.Document.SnapshotPath || document.SnapshotSha256 != data.Document.SnapshotSha256;
            document.SnapshotPath = data.Document.SnapshotPath; document.SnapshotSha256 = data.Document.SnapshotSha256; ApplyAccount(data);
            if (changed) { document.ConfirmedFor = null; execution.ConfirmedFor = null; ExecutionModeChanged?.Invoke(this, EventArgs.Empty); }
            status.Text = changed ? "已选用账号资料，编辑内容保留；请重新核对五人和操作并保存。" : "账号资料保持不变。";
        }, "正在核对账号资料…");
    }
    public async Task<bool> UseAccountSnapshotAsync(PcrGuideSnapshot snapshot)
    {
        var applied = false; var value = Clone(document);
        await QueueDisplay(() =>
        {
            var path = PcrClanGuide.SnapshotPaths(configDirectory).SingleOrDefault(candidate => PcrClanGuide.Stamp(candidate)?.Equals(snapshot.Sha256, StringComparison.OrdinalIgnoreCase) == true)
                ?? throw new InvalidDataException("所选账号缓存已改变，请重新读取。");
            value.SnapshotPath = path; value.SnapshotSha256 = snapshot.Sha256.ToLowerInvariant();
            var data = PrepareDisplay(value, false);
            if ((string?)data.Snapshot?["account_id"] != snapshot.AccountId || (string?)data.Snapshot?["server"] != snapshot.Server || (string?)data.Snapshot?["content_version"] != snapshot.ContentVersion)
            { data.Dispose(); throw new InvalidDataException("所选资料与账号身份不一致。"); }
            return data;
        }, data =>
        {
            var changed = document.SnapshotPath != data.Document.SnapshotPath || document.SnapshotSha256 != data.Document.SnapshotSha256;
            document.SnapshotPath = data.Document.SnapshotPath; document.SnapshotSha256 = data.Document.SnapshotSha256; ApplyAccount(data); applied = true;
            if (changed) { document.ConfirmedFor = null; execution.ConfirmedFor = null; ExecutionModeChanged?.Invoke(this, EventArgs.Empty); }
            status.Text = changed ? "已使用账号资料，编辑内容保留；请重新核对作业和三刀。" : "账号资料保持不变。";
        }, "正在核对所选账号资料…");
        return applied;
    }
    public Task RefreshAccountsAsync()
    {
        var value = Clone(document);
        return QueueDisplay(() => PrepareDisplay(value, false), data => { ApplyAccount(data); status.Text = "账号资料列表已刷新；当前选择和编辑内容保留。"; }, "正在读取账号资料列表…");
    }
    private Task PersistAsync(bool confirm)
    {
        if (!loaded || loading || dailyPlan.IsLoading) { status.Text = "资料尚未载入完成，请稍候再保存。"; return Task.CompletedTask; }
        var value = Capture(); var expected = loadedHash; var version = ++loadVersion; SetLoading(true); status.Text = "正在核对并保存…";
        return pendingLoad = Complete();
        async Task Complete()
        {
            string? saved = null, warning = null;
            try
            {
                await Task.Run(() =>
                {
                    saved = PcrClanGuide.Save(value, statePath, expected, configDirectory, repository, DateTimeOffset.Now, confirm);
                    if (confirm) { try { PcrClanGuide.SavePublicGuide(value, configDirectory, repository); } catch (Exception ex) { warning = ex.Message; } }
                }).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    loadedHash = saved;
                    if (version != loadVersion) return;
                    document = value;
                    status.Text = warning != null ? "本队已确认保存；公共攻略库未保存：" + warning : !confirm ? "草稿已保存，尚未确认执行。"
                        : executionLoaded && execution.Mode == PcrClanDailyExecution.Simulation ? "已核对并保存。点击开始任务执行一场模拟；缺少对应校准时会停止。"
                        : "本队已核对并保存。正式三刀还需单独核对并保存完整三目标。";
                });
            }
            catch (Exception ex) { await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) status.Text = "未完成：" + ex.Message; }); }
            finally { await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) SetLoading(false); }); }
        }
    }
    public Task LoadDailySlotAsync(int slot) => dailyPlan.LoadSlotAsync(slot);
    private Task LoadReviewedAsync(PcrClanGuideDocument value, string? expectedHash, Func<bool> isCurrent)
    {
        if (!loaded || loadedHash != expectedHash) throw new IOException("原作业已被其他窗口修改，请重开后载入；当前队伍未改变。");
        var version = ++loadVersion; var showImages = sourceDetails.IsExpanded; SetLoading(true); status.Text = "正在载入并核对本刀…";
        return pendingLoad = Complete();
        async Task Complete()
        {
            DisplayData? data = null; string? savedHash = null;
            try
            {
                data = await Task.Run(() =>
                {
                    var next = PcrClanGuide.Bundle(value.BundlePath);
                    using (var source = new Bitmap(new MemoryStream(PcrGuideData.ReadLocal((string)value.Job["source"]!["image_path"]!)))) { }
                    if (!isCurrent() || version != System.Threading.Volatile.Read(ref loadVersion)) return null;
                    savedHash = PcrClanGuide.Save(value, statePath, expectedHash, configDirectory, repository, DateTimeOffset.Now, true);
                    var (saved, readHash) = ReadSavedGuide(statePath);
                    var plan = PcrClanGuide.CreatePlan(statePath, configDirectory, repository, DateTimeOffset.Now);
                    if (readHash != savedHash || saved.SelectedJobId != value.SelectedJobId || saved.ConfirmedFor != (string?)plan["confirmed_for"])
                        throw new IOException("当前模拟队写后核对失败，请重新打开。");
                    var result = PrepareDisplay(saved, showImages); result.Bundle = next; result.StateStamp = readHash!; return result;
                }).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (savedHash != null) loadedHash = savedHash;
                    if (data == null || !isCurrent() || version != loadVersion) return;
                    bundle = data.Bundle; cacheNotice.Text = ""; ApplyDisplay(data); SynchronizeSelection(); loaded = true;
                    status.Text = "已载入本刀并保存为当前编辑队伍。";
                });
            }
            catch (Exception ex)
            {
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (savedHash != null) { loadedHash = savedHash; loaded = false; }
                    if (version == loadVersion) { document.ConfirmedFor = null; status.Text = "当前模拟队载入失败，请重新打开核对：" + ex.Message; }
                });
                throw;
            }
            finally { data?.Dispose(); await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) SetLoading(false); }); }
        }
    }
}

using System;
using System.IO;
using System.Linq;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// The same owner and locks span startup, channel revocation and child cleanup.
public static partial class PcrAlphaOwner
{
    private sealed class Session(MaaProcessor processor, JObject selection, ValidatedConfig config, PcrStartConsent consent)
    {
        // Capture the test-only construction seam per session. Changing static
        // hooks later cannot turn a production session into an offline one.
        public readonly Func<JObject, MaaController>? OfflineControllerBuilder = OfflineAgentScript != null ? ControllerBuilder : null;
        public readonly Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? MuMuFactory = OfflineMuMuFactory;
        public readonly string? AgentScript = OfflineAgentScript;
        public readonly Func<PcrDisplayRead, CancellationToken, Task<string>>? DisplayReader = OfflineDisplayReader;
        public readonly CancellationTokenSource Cancel = new();
        public bool ValidatePhysicalViewport { get; init; } = true;
        public PcrMuMuDisplay.Mapping? Mapping;
        public TimeSpan MappingChecked;
        public Task? MappingWatch;
        public GuiTarget? Target;
        public bool Preparing;
        public bool IsOffline => AgentScript != null && ((OfflineControllerBuilder != null) ^ (MuMuFactory != null));
        public readonly MaaProcessor Processor = processor;
        public readonly JObject Selection = (JObject)selection.DeepClone();
        public readonly ValidatedConfig Config = config;
        public Action<string>? ReportSummary { get; init; }
        public Action<JObject>? ReportPlayerLog { get; init; }
        public string? DiagnosticRunId { get; init; }
        public string? SelectedConfigPath { get; init; }
        public string? ConfigFileHash { get; init; }
        public bool PreparationOnly { get; init; }
        public int MaxSeconds => !PreparationOnly && Selection["selected_modules"] is JArray { Count: 1 } modules
            && (string?)modules[0] == "clan_battle_daily" ? 3600 : 1800;
        public readonly PcrStartConsent Consent = consent;
        public readonly PcrClanCodeBinding? ClanCode = selection["selected_modules"] is JArray { Count: 1 } modules
            && (string?)modules[0] == "clan_battle_daily" ? PcrClanCodeBinding.Capture(repository, (JObject)JObject.Parse(config.Json)["config"]!) : null;
        public readonly System.Collections.Generic.HashSet<string> ClanRequests = new(StringComparer.Ordinal);
        public readonly Stopwatch Watch = Stopwatch.StartNew();
        public readonly string Invocation = Guid.NewGuid().ToString("N");
        public bool Revoked, Started, Appending, Consumed, RawVerified, ConstructionAttempted, CleanupUnconfirmed;
        public long Root;
        public string Uuid = "";
        public MaaController? Controller;
        public PcrCapturePerformance? CapturePerformance;
        public MaaTasker? BoundTasker;
        public MaaResource? BoundResource;
        public ChildSession? Child;
    }
    private sealed class ChildSession : IPlatformAgentSession, IPlatformAgentStartupStatus, IPlatformAgentShutdown
    {
        private readonly PcrWindowsChild process = new();
        private readonly Session session;
        private int disposed;
        private readonly object exitGate = new();
        private readonly PcrAgentShutdown shutdown = new();
        private int faultStarted;
        private Task? faultCleanup;
        private readonly PlatformAgentStartRequest request;
        public bool ShutdownForced => shutdown.Forced;
        public bool ShutdownQuiescent => shutdown.Quiescent;
        public bool FaultCleanupComplete => Volatile.Read(ref faultStarted) == 0 || faultCleanup?.IsCompleted == true;
        public bool TryShutdown(Action operation) => shutdown.TryRun(operation, Dispose);
        public string? StartupFailure
        {
            get
            {
                lock (exitGate)
                    return disposed != 0 ? "PCR Agent session disposed" : process.StartupFailure;
            }
        }
        public void CompleteStartup()
        {
            lock (exitGate)
            {
                if (disposed != 0) throw new ObjectDisposedException(nameof(ChildSession));
                process.VerifyMembership();
                if (StartupFailure is { } failure) throw new InvalidOperationException(failure);
            }
            var port = ReadConnectionAsync().GetAwaiter().GetResult();
            _ = Task.Run(() => WatchConnectionAsync(port));
        }
        private async Task<PcrAgentConnection.Peer> ReadConnectionAsync()
        {
            var cancellation = session.Cancel.Token;
            cancellation.ThrowIfCancellationRequested();
            var read = Task.Run(() =>
            {
                int[] members;
                lock (exitGate)
                {
                    if (disposed != 0 || process.Exited) throw new InvalidOperationException("Owned Agent creation handle exited");
                    members = process.ProcessIds;
                }
                var peer = PcrAgentConnection.EstablishedConnection(members, int.Parse(request.Identifier));
                lock (exitGate)
                    if (disposed != 0 || process.Exited || Array.IndexOf(process.ProcessIds, peer.ProcessId) < 0)
                        throw new InvalidOperationException("Owned Agent process binding changed");
                return peer;
            });
            try
            {
                // A slow OS snapshot is not a disconnected Peer; keep one read until it returns or this Owner stops.
                var peer = await read.WaitAsync(cancellation).ConfigureAwait(false);
                cancellation.ThrowIfCancellationRequested();
                return peer;
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
            {
                _ = read.ContinueWith(completed => { _ = completed.Exception; }, CancellationToken.None,
                    TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
                throw;
            }
        }
        private async Task WatchConnectionAsync(PcrAgentConnection.Peer port)
        {
            try
            {
                while (!session.Cancel.IsCancellationRequested)
                {
                    await Task.Delay(1000, session.Cancel.Token).ConfigureAwait(false);
                    if (await ReadConnectionAsync().ConfigureAwait(false) != port)
                        throw new InvalidOperationException("Owned Agent TCP connection changed");
                }
            }
            catch (OperationCanceledException) when (session.Cancel.IsCancellationRequested) { }
            catch (Exception ex)
            {
                FaultSession(ex.Message);
            }
        }
        private void FaultSession(string reason)
        {
            lock (Gate)
                if (current != session || session.Cancel.IsCancellationRequested
                    || Interlocked.Exchange(ref faultStarted, 1) != 0) return;
            faultCleanup = Task.Run(() =>
            {
                try { Revoke(session.Processor); }
                catch (Exception ex) { reason += "; revoke: " + ex.GetType().Name; }
                try
                {
                    // A queued GUI command can be behind the blocked root task.
                    var tasker = session.BoundTasker;
                    if (tasker != null) TryShutdown(() => tasker.Stop().Wait());
                    if (request.Disconnect != null) TryShutdown(request.Disconnect);
                }
                catch (Exception ex) { reason += "; stop: " + ex.GetType().Name; }
                try { Dispose(); }
                catch (Exception ex) { reason += "; child exit: " + ex.GetType().Name; }
                try { session.Processor.Stop(MFAAvalonia.Helper.ValueType.MFATask.MFATaskStatus.FAILED); }
                catch (Exception ex) { reason += "; GUI stop: " + ex.GetType().Name; }
                try { request.Output("error: Agent 连接异常，结果未确认；已停止并禁止重新开始。请关闭后重开程序，保留未完成记录。原因：" + reason); }
                catch { } // Diagnostic delivery cannot prevent shutdown supervision.
            });
        }
        public void RevokeAuthorization()
        {
            lock (exitGate)
            {
                // Existing grants are rejected by the next live source probe
                // and native Stop. Keep EOF in the established disposal order;
                // killing the server before AgentClient.Disconnect can deadlock.
                if (disposed == 0 && !process.RevokeGrants()) Dispose();
            }
        }
        public void RevokeInput()
        {
            lock (exitGate)
            {
                session.Revoked = true;
                if (disposed == 0) { process.CloseInput(); RevokeAuthorization(); }
            }
        }
        public ChildSession(Session owner, PlatformAgentStartRequest request, string root, string script)
        {
            session = owner;
            this.request = request;
            // Publish before the first native allocation: even a failed constructor
            // must leave an owner that Release can retry, blocking a new Start.
            owner.Child = this;
            void RevokeChannel(Task ended)
            {
                _ = ended.Exception;
                try
                {
                    lock (Gate) { owner.Revoked = true; Monitor.PulseAll(Gate); }
                    try { RevokeAuthorization(); }
                    finally { FaultSession("owned Agent channel ended"); }
                }
                catch (Exception ex) { request.Output("error: PCR owner revoke: " + ex.GetType().Name); }
            }
            try
            {
                var arguments = owner.SelectedConfigPath is { } selectedPath
                    ? new[] { "-B", script, request.Identifier, selectedPath }
                    : new[] { "-B", script, request.Identifier };
                if (owner.DiagnosticRunId is { } logId && Path.GetFileName(script) == "start-alpha-owned-agent.py")
                    arguments = arguments.Concat(new[] { "--player-log", logId,
                        owner.Selection["selected_modules"] is JArray { Count: 1 } chosen && ((string?)chosen[0])?.StartsWith("clan_battle_",StringComparison.Ordinal) == true ? "clan" : "daily" }).ToArray();
                process.Start(PcrRuntimePaths.ResolvePython(root), root, arguments);
                _ = process.Send(owner.Config.Json).ContinueWith(RevokeChannel,
                    CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted, TaskScheduler.Default);
            }
            catch
            {
                owner.Revoked = true;
                Dispose(); // throws and retains this owner if exit/EOF is unproved
                throw;
            }
            // Revocation must not wait for stderr EOF. These continuations are
            // separate from the pipe tasks that Dispose waits on under Gate.
            _ = process.StdoutCompletion.ContinueWith(RevokeChannel, TaskScheduler.Default);
            _ = process.StderrCompletion.ContinueWith(RevokeChannel,
                CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted, TaskScheduler.Default);
            _ = Task.Run(async () =>
            {
                try
                {
                    // Pipe pumps never enter Gate or call GUI code. Release may
                    // hold Gate while waiting for native exit and both pipe EOFs.
                    await foreach (var (error, line) in process.Lines.Reader.ReadAllAsync())
                    {
                        if (error) request.Output("error: " + line);
                        else if (line.StartsWith("pcr-owner-request:", StringComparison.Ordinal))
                        {
                            var reply = Consume(JObject.Parse(line[18..]));
                            await ((bool?)reply["ok"] == true
                                ? process.SendGrant(reply.ToString(Formatting.None))
                                : process.Send(reply.ToString(Formatting.None)));
                        }
                        else if (line.StartsWith("pcr-owner-source:", StringComparison.Ordinal))
                        {
                            var reply = CheckSource(JObject.Parse(line[17..]));
                            await ((bool?)reply["ok"] == true
                                ? process.SendGrant(reply.ToString(Formatting.None))
                                : process.Send(reply.ToString(Formatting.None)));
                        }
                        else if (line.StartsWith(PcrClanConsumptionConsent.Prefix, StringComparison.Ordinal))
                        {
                            var reply = await ConfirmClanConsumptionAsync(session, PcrClanConsumptionConsent.Parse(line[PcrClanConsumptionConsent.Prefix.Length..]));
                            await ((bool?)reply["ok"] == true ? process.SendGrant(reply.ToString(Formatting.None)) : process.Send(reply.ToString(Formatting.None)));
                        }
                        else if (line.StartsWith("pcr-player-log:", StringComparison.Ordinal))
                        {
                            try { if (line.Length <= 16384) owner.ReportPlayerLog?.Invoke(JObject.Parse(line[15..])); }
                            catch { /* Display-only packets never revoke an Owner. */ }
                        }
                        else if (line.StartsWith("info:") || line.StartsWith("error:") || line.StartsWith("warning:"))
                        {
                            request.Output(line);
                            owner.ReportSummary?.Invoke(line);
                        }
                    }
                }
                catch (Exception ex) { request.Output("error: PCR owner channel: " + ex.GetType().Name); }
                finally { RevokeChannel(Task.CompletedTask); }
            });
        }
        private JObject CheckSource(JObject request)
        {
            lock (Gate)
            {
                var reply = (JObject)request.DeepClone();
                var valid = session == current && session.Consumed && !session.Revoked
                    && session.Watch.Elapsed.TotalSeconds < session.MaxSeconds
                    && (session.Mapping == null || (session.Watch.Elapsed - session.MappingChecked).TotalSeconds <= 15)
                    && (long?)request["root_task_id"] == session.Root
                    && (string?)request["controller_uuid"] == session.Uuid
                    && (string?)request["invocation_id"] == session.Invocation
                    && Guid.TryParseExact((string?)request["probe_id"], "N", out _)
                    && BindingUnchanged(session);
                if (!valid) session.Revoked = true;
                reply["ok"] = valid;
                return reply;
            }
        }
        private JObject Consume(JObject request)
        {
            lock (Gate)
            {
                // Callback can precede AppendTask's managed return. Never call the
                // SDK under this lock; registration has a bounded wait, no retry.
                var wait = Stopwatch.StartNew();
                while (session.Root == 0 && !session.Revoked && wait.Elapsed.TotalSeconds < 2)
                    Monitor.Wait(Gate, 20);
                if (session != current || session.PreparationOnly || session.Preparing || !session.IsOffline && session.Mapping == null || session.Revoked || session.Consumed || session.Root == 0
                    || session.Mapping != null && (session.Watch.Elapsed - session.MappingChecked).TotalSeconds > 15
                    || !BindingUnchanged(session)
                    || session.Watch.Elapsed.TotalSeconds >= session.MaxSeconds
                    || (long?)request["root_task_id"] != session.Root || (string?)request["controller_uuid"] != session.Uuid
                    || !JToken.DeepEquals(request["combination"], session.Selection))
                { session.Revoked = true; return new JObject { ["ok"] = false }; }
                session.Consumed = true; Grants++;
                var binding = session.Config.Binding();
                var reply = new JObject { ["ok"] = true, ["fixed_binding"] = true, ["invocation_id"] = session.Invocation,
                    ["timing_owner_pid"] = Environment.ProcessId,
                    ["timing_owner_log"] = Path.GetFullPath(Path.Combine("debug", "maafw.log")),
                    ["root_task_id"] = session.Root, ["controller_uuid"] = session.Uuid,
                    ["serial"] = binding["connection"]!["serial"],
                    ["combination"] = session.Selection.DeepClone(), ["execute"] = true,
                    ["game_day"] = session.Consent.GameDay, ["count_context"] = session.Consent.CountContext,
                    ["remaining_seconds"] = session.MaxSeconds - session.Watch.Elapsed.TotalSeconds,
                    ["schedule_authorizations"] = new JArray(session.Consent.ScheduleAuthorizations),
                    ["schedule_attest_current_plan"] = session.Consent.AttestCurrentPlan,
                    ["schedule_confirm_new_cycle"] = session.Consent.ConfirmNewCycle };
                if (session.ClanCode != null) reply["owner_core_sha256"] = session.ClanCode.Core;
                if (binding["controller"] != null) reply["controller"] = binding["controller"]!.DeepClone();
                else if (binding["mumu"] != null) reply["mumu"] = binding["mumu"]!.DeepClone();
                else
                {
                    reply["input_display"] = binding["device"]!["input_display"];
                    reply["screenshot_display"] = binding["device"]!["screenshot_display"];
                }
                return reply;
            }
        }
        public void Dispose()
        {
            lock (exitGate)
            {
                if (disposed != 0) return;
                session.Revoked = true;
                process.Dispose();
                disposed = 1; // only after Job zero, original root Wait and dual EOF
            }
        }
    }
}

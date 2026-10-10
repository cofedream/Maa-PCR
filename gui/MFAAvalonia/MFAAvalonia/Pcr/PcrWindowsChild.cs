using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

namespace MFAAvalonia.Pcr;

// One private, non-breakaway Job. Authority is the Job and CreateProcess handles,
// never a reopened PID. The owning Session retains this object after any failure.
internal sealed class PcrWindowsChild : IDisposable
{
    private IntPtr job, process, thread;
    private Stream? input;
    private readonly object inputGate = new();
    private Task writes = Task.CompletedTask;
    private Task grantWrite = Task.CompletedTask;
    private bool grantsRevoked;
    private Task stdout = Task.CompletedTask, stderr = Task.CompletedTask;
    internal Task StdoutCompletion => stdout;
    internal Task StderrCompletion => stderr;
    private bool closing, released;
    internal readonly Channel<(bool Error, string Text)> Lines = Channel.CreateUnbounded<(bool, string)>();
    internal int Id { get; private set; }
    internal bool Resumed { get; private set; }
    // Reflection-only offline fault seams; no public launch or permission surface.
    private static Func<string, IntPtr, IntPtr> operationHandle = (_, h) => h;
    private static Action<string> checkpoint = _ => { };
    private static Action<IntPtr> terminate = h => Check(TerminateJobObject(h, 1), "TerminateJobObject");
    internal bool Exited => process != IntPtr.Zero && Wait(process, 0);
    internal string? StartupFailure
    {
        get
        {
            if (writes.IsFaulted) return "PCR Agent stdin failed: " + writes.Exception!.GetBaseException().Message;
            if (stdout.IsFaulted || stderr.IsFaulted) return "PCR Agent pipe read failed";
            if (!Exited) return null;
            Check(GetExitCodeProcess(process, out var code), "GetExitCodeProcess");
            return "PCR Agent exited " + code;
        }
    }
    internal uint Active
    {
        get
        {
            if (job == IntPtr.Zero) return 0;
            Check(QueryInformationJobObject(operationHandle("query", job), 1, out Accounting a,
                (uint)Marshal.SizeOf<Accounting>(), IntPtr.Zero), "QueryInformationJobObject");
            return a.Active;
        }
    }
    internal void VerifyMembership()
    {
        Check(IsProcessInJob(process, operationHandle("membership", job), out var member), "IsProcessInJob");
        if (!member) throw new InvalidOperationException("Created Agent is not in its private Job");
        _ = Active;
    }
    internal void Start(string exe, string root, params string[] args)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("PCR Alpha requires Windows JobList");
        if (job != IntPtr.Zero || released) throw new InvalidOperationException("Child launch is single use");
        IntPtr attrs = IntPtr.Zero, jobs = IntPtr.Zero, handles = IntPtr.Zero;
        IntPtr stdinChild = IntPtr.Zero, stdoutChild = IntPtr.Zero, stderrChild = IntPtr.Zero;
        bool initialized = false;
        try
        {
            job = CreateJobObjectW(IntPtr.Zero, null); Check(job != IntPtr.Zero, "CreateJobObjectW");
            var limits = new ExtendedLimit { Basic = new BasicLimit { Flags = 0x2000 } };
            Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf<ExtendedLimit>()), "SetInformationJobObject");
            input = Pipe(true, out stdinChild);
            var outputStream = Pipe(false, out stdoutChild);
            stdout = Drain(outputStream, false);
            var errorStream = Pipe(false, out stderrChild);
            stderr = Drain(errorStream, true);
            _ = Task.WhenAll(stdout, stderr).ContinueWith(t => Lines.Writer.TryComplete(t.Exception), TaskScheduler.Default);
            checkpoint("stdio");
            nuint size = 0;
            InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);
            if (size == 0) throw new Win32Exception(Marshal.GetLastWin32Error(), "Attribute size");
            attrs = Marshal.AllocHGlobal(checked((int)size));
            Check(InitializeProcThreadAttributeList(attrs, 2, 0, ref size), "InitializeProcThreadAttributeList");
            initialized = true;
            jobs = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(jobs, operationHandle("job-list", job));
            handles = Marshal.AllocHGlobal(3 * IntPtr.Size);
            Marshal.WriteIntPtr(handles, 0, stdinChild); Marshal.WriteIntPtr(handles, IntPtr.Size, stdoutChild);
            Marshal.WriteIntPtr(handles, 2 * IntPtr.Size, stderrChild);
            Check(UpdateProcThreadAttribute(attrs, 0, 0x2000d, jobs, (nuint)IntPtr.Size, IntPtr.Zero, IntPtr.Zero), "JOB_LIST");
            Check(UpdateProcThreadAttribute(attrs, 0, 0x20002, handles, (nuint)(3 * IntPtr.Size), IntPtr.Zero, IntPtr.Zero), "HANDLE_LIST");
            checkpoint("attributes");
            var si = new StartupEx { Attributes = attrs, Info = new Startup { Size = (uint)Marshal.SizeOf<StartupEx>(),
                Flags = 0x100, Input = stdinChild, Output = stdoutChild, Error = stderrChild } };
            var command = new StringBuilder(string.Join(" ", new[] { exe }.Concat(args).Select(Quote)));
            Check(CreateProcessW(exe, command, IntPtr.Zero, IntPtr.Zero, true, 0x08080004,
                IntPtr.Zero, root, ref si, out var pi), "CreateProcessW JOB_LIST");
            process = pi.Process; thread = pi.Thread; Id = (int)pi.Id;
            VerifyMembership(); // original creation handle, before any user code
            var previous = ResumeThread(operationHandle("resume", thread));
            if (previous == uint.MaxValue) throw new Win32Exception(Marshal.GetLastWin32Error(), "ResumeThread");
            if (previous != 1) throw new InvalidOperationException("Unexpected suspend count; no second Resume");
            Resumed = true;
        }
        finally
        {
            // Child copies are now the only writers. Do this even when creation fails.
            Close(ref stdinChild); Close(ref stdoutChild); Close(ref stderrChild); Close(ref thread);
            if (initialized) DeleteProcThreadAttributeList(attrs);
            if (attrs != IntPtr.Zero) Marshal.FreeHGlobal(attrs);
            if (jobs != IntPtr.Zero) Marshal.FreeHGlobal(jobs);
            if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
        }
    }
    private Task Drain(Stream stream, bool error) => Task.Run(async () =>
    {
        using var reader = new StreamReader(stream, Encoding.UTF8);
        while (await reader.ReadLineAsync().ConfigureAwait(false) is { } line)
            if (!Lines.Writer.TryWrite((error, line))) throw new IOException("Owner output queue closed");
    });
    internal Task Send(string text) => QueueInput(text, false);
    internal Task SendGrant(string text) => QueueInput(text, true);
    private Task QueueInput(string text, bool positive)
    {
        lock (inputGate)
        {
            if (closing || input == null) throw new InvalidOperationException("Owner input is closed");
            // Anonymous pipes use blocking handles; schedule off the UI/owner lock.
            // One chain preserves config-before-grant and never writes concurrently.
            writes = writes.ContinueWith(t =>
            {
                t.GetAwaiter().GetResult();
                TaskCompletionSource? delivery = null;
                if (positive)
                {
                    checkpoint("grant-before-commit");
                    lock (inputGate)
                    {
                        if (closing) return; // EOF is the terminal refusal during cleanup.
                        if (grantsRevoked) text = "{\"ok\":false}";
                        else
                        {
                            // Commit versus revoke is serialized, but no pipe IO
                            // holds this lock. Revoke must also join this write.
                            delivery = new(TaskCreationOptions.RunContinuationsAsynchronously);
                            grantWrite = delivery.Task;
                        }
                    }
                }
                try
                {
                    if (delivery != null) checkpoint("grant-writing");
                    // No managed output buffer: a failed/partial grant must never
                    // be retried by a later Flush or Dispose after revoke returns.
                    input.Write(Encoding.UTF8.GetBytes(text + Environment.NewLine));
                }
                finally { delivery?.TrySetResult(); }
            }, TaskScheduler.Default);
            return writes;
        }
    }
    internal bool RevokeGrants()
    {
        Task pending;
        lock (inputGate) { grantsRevoked = true; pending = grantWrite; }
        // Success means all committed writes ended before return. A full pipe
        // cannot hold Gate forever: the caller must terminate/verify or fail.
        return pending.Wait(1000);
    }
    internal void CloseInput()
    {
        lock (inputGate)
        {
            if (closing) return;
            grantsRevoked = true;
            closing = true;
            writes = writes.ContinueWith(t => { input?.Dispose(); t.GetAwaiter().GetResult(); }, TaskScheduler.Default);
        }
    }
    public void Dispose()
    {
        if (released) return;
        CloseInput();
        var grace = Stopwatch.StartNew();
        while (Active != 0 && grace.ElapsedMilliseconds < 1000) Thread.Sleep(10);
        if (Active != 0) terminate(operationHandle("terminate", job));
        // A failed native call never authorizes release, even if a later retry can
        // establish exit. Keep KILL_ON_JOB_CLOSE as a last-resort lifetime guard.
        var deadline = Stopwatch.StartNew();
        while (Active != 0)
        {
            if (deadline.ElapsedMilliseconds >= 5000) throw new TimeoutException("Private Job did not become empty");
            Thread.Sleep(10);
        }
        if (process != IntPtr.Zero && !Wait(process, Math.Max(0, 5000 - (int)deadline.ElapsedMilliseconds)))
            throw new TimeoutException("Created Agent handle did not signal exit");
        var pipes = Task.WhenAll(stdout, stderr);
        if (!pipes.Wait(Math.Max(0, 5000 - (int)deadline.ElapsedMilliseconds)))
            throw new TimeoutException("Owned stdout/stderr EOF not confirmed");
        // A broken stdin write is expected after termination; its completion is
        // required before handles are released, its exception is still observable.
        if (!((IAsyncResult)writes).AsyncWaitHandle.WaitOne(Math.Max(0, 5000 - (int)deadline.ElapsedMilliseconds)))
            throw new TimeoutException("Owned stdin writer did not finish");
        _ = writes.Exception;
        _ = Active; // propagate final query failures too
        Close(ref process); Close(ref job);
        released = true;
    }
    private static FileStream Pipe(bool input, out IntPtr child)
    {
        var sa = new Security { Size = (uint)Marshal.SizeOf<Security>(), Inherit = true };
        Check(CreatePipe(out var read, out var write, ref sa, 0), "CreatePipe");
        child = input ? read : write;
        var parent = input ? write : read;
        try
        {
            Check(SetHandleInformation(parent, 1, 0), "Noninheritable parent pipe");
            return new FileStream(new SafeFileHandle(parent, true), input ? FileAccess.Write : FileAccess.Read,
                bufferSize: input ? 1 : 4096);
        }
        catch { Close(ref parent); Close(ref child); throw; }
    }
    private static string Quote(string value)
    {
        var b = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            b.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); b.Append(c); slashes = 0;
        }
        return b.Append('\\', slashes * 2).Append('"').ToString();
    }
    private static void Check(bool ok, string name) { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error(), name); }
    private static bool Wait(IntPtr h, int ms)
    {
        uint result = WaitForSingleObject(h, (uint)ms);
        if (result == 0) return true;
        if (result == 258) return false;
        throw new Win32Exception(Marshal.GetLastWin32Error(), "WaitForSingleObject");
    }
    private static void Close(ref IntPtr h) { if (h == IntPtr.Zero) return; Check(CloseHandle(h), "CloseHandle"); h = IntPtr.Zero; }
    [StructLayout(LayoutKind.Sequential)] private struct Security { public uint Size; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit; }
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimit { public long ProcessTime, JobTime; public uint Flags; public nuint Min, Max; public uint Active; public nuint Affinity; public uint Priority, Scheduling; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit { public BasicLimit Basic; public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; public nuint ProcessMemory, JobMemory, PeakProcess, PeakJob; }
    [StructLayout(LayoutKind.Sequential)] private struct Accounting { public long User, Kernel, PeriodUser, PeriodKernel; public uint Faults, Total, Active, Terminated; }
    [StructLayout(LayoutKind.Sequential)] private struct Startup { public uint Size; public IntPtr Reserved, Desktop, Title; public uint X, Y, Width, Height, XChars, YChars, Fill, Flags; public ushort Show, ReservedSize; public IntPtr Reserved2, Input, Output, Error; }
    [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup Info; public IntPtr Attributes; }
    [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr Process, Thread; public uint Id, ThreadId; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObjectW(IntPtr sa, string? name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimit value, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool QueryInformationJobObject(IntPtr job, int kind, out Accounting value, uint size, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref Security sa, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref nuint size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, nuint attr, IntPtr value, nuint size, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CreateProcessW(string exe, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string cwd, ref StartupEx startup, out ProcessInfo process);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
}

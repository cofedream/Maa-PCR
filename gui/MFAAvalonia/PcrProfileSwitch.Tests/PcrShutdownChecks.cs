using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Threading;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.Windows;
using MFAAvalonia.Views.Windows;

internal static class PcrShutdownChecks
{
    private const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;

    internal static void Run()
    {
        var owner = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner", true)!;
        var current = owner.GetField("current", Hidden)!;
        var gate = owner.Assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate", true)!;
        var updatePending = gate.GetField("UpdatePending", Hidden)!;
        var shutdownPending = gate.GetField("ShutdownPending", Hidden)!;
        var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", Hidden)!.GetValue(null)!;
        var hadRoot = cache.TryGetValue(typeof(RootViewModel), out var oldRoot);
        var previousRun = current.GetValue(null);
        var previousUpdate = updatePending.GetValue(null);
        var previousShutdown = shutdownPending.GetValue(null);
        var previousContext = SynchronizationContext.Current;
        var checks = 0;
        void Require(bool value, string message)
        {
            if (!value) throw new InvalidOperationException("pcr_shutdown: " + message);
            checks++;
        }
        var rootModel = (RootViewModel)RuntimeHelpers.GetUninitializedObject(typeof(RootViewModel));
        var view = (RootView)RuntimeHelpers.GetUninitializedObject(typeof(RootView));
        var requiresConfirmation = typeof(RootView).GetProperty("RequiresExitConfirmation", Hidden)!;
        var request = typeof(RootView).GetMethod("RequestExitAsync", Hidden, [typeof(Func<Task<bool>>), typeof(Action)])!;
        var confirmed = typeof(RootView).GetField("_exitConfirmed", Hidden)!;
        var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        typeof(TaskQueueViewModel).GetField("_labyrinthSettings", Hidden)!.SetValue(vm,
            RuntimeHelpers.GetUninitializedObject(typeof(PcrLabyrinthSettings)));
        var runType = owner.GetNestedType("Run", BindingFlags.NonPublic)!;
        var run = Activator.CreateInstance(runType, Hidden | BindingFlags.Public, null, [vm], null)!;
        using var cancel = (CancellationTokenSource)runType.GetField("Cancel", Hidden)!.GetValue(run)!;
        var completion = (TaskCompletionSource)runType.GetField("Completion", Hidden)!.GetValue(run)!;
        Task Exit(Func<Task<bool>> confirm, Action shutdown) => (Task)request.Invoke(view, [confirm, shutdown])!;
        try
        {
            SynchronizationContext.SetSynchronizationContext(new AvaloniaSynchronizationContext());
            cache[typeof(RootViewModel)] = new Lazy<object>(() => rootModel);
            current.SetValue(null, null);
            shutdownPending.SetValue(null, false);
            updatePending.SetValue(null, true);
            Require(!(bool)requiresConfirmation.GetValue(view)!, "pending update alone must not prompt as running work");
            updatePending.SetValue(null, false);
            current.SetValue(null, run);
            Require(!rootModel.IsRunning && (bool)requiresConfirmation.GetValue(view)!,
                "active labyrinth must require confirmation while the ordinary task queue is idle");

            var prompts = 0;
            var exits = 0;
            var declined = Exit(() => { prompts++; return Task.FromResult(false); }, () => exits++);
            Require(declined.IsCompletedSuccessfully && prompts == 1 && exits == 0
                && !cancel.IsCancellationRequested && ReferenceEquals(current.GetValue(null), run)
                && !(bool)shutdownPending.GetValue(null)!,
                "declining exit must leave the owner running");

            var stopping = Exit(() => { prompts++; return Task.FromResult(true); }, () => exits++);
            Require(cancel.IsCancellationRequested && vm.LabyrinthSettings.Progress.Contains("记账"),
                "confirmed exit must use the owner's normal stop path");
            Require(!stopping.IsCompleted && exits == 0 && !(bool)confirmed.GetValue(view)!,
                "stop requested is not cleanup complete and must not start shutdown");
            Require((bool)shutdownPending.GetValue(null)! && !vm.CanEditPcrTarget,
                "confirmed exit must block new starts and target changes");
            Require(Exit(() => throw new InvalidOperationException("duplicate prompt"), () => exits++).IsCompletedSuccessfully,
                "repeated window or tray exit must not duplicate the pending shutdown");

            current.SetValue(null, null);
            Require(!stopping.IsCompleted && exits == 0, "owner release alone must wait for its final cleanup completion");
            var lateLabyrinth = (Task)owner.GetMethod("RequestStartAsync", Hidden)!.Invoke(null, [vm])!;
            var lateDaily = PcrAlphaOwner.RequestStartAsync(vm);
            Require(lateLabyrinth.IsCompletedSuccessfully && lateDaily.IsCompletedSuccessfully
                && current.GetValue(null) == null && !vm.CanEditPcrTarget,
                "owner release before completion must not allow another labyrinth or daily start");
            completion.SetResult();
            var timeout = Stopwatch.StartNew();
            while (!stopping.IsCompleted && timeout.Elapsed < TimeSpan.FromSeconds(5))
            {
                Dispatcher.UIThread.RunJobs();
                Thread.Sleep(1);
            }
            Require(stopping.IsCompletedSuccessfully && exits == 1 && prompts == 2 && (bool)confirmed.GetValue(view)!
                && (bool)shutdownPending.GetValue(null)!,
                "shutdown is allowed exactly once after cleanup completes");

            // The preceding final callback is synthetic, so reset the successful exit before testing a failed one.
            shutdownPending.SetValue(null, false);
            view = (RootView)RuntimeHelpers.GetUninitializedObject(typeof(RootView));
            var failed = Exit(() => Task.FromResult(true), () => throw new InvalidOperationException("synthetic shutdown failure"));
            Require(failed.IsFaulted && failed.Exception?.InnerException is InvalidOperationException
                && !(bool)shutdownPending.GetValue(null)! && !(bool)confirmed.GetValue(view)!,
                "failed final shutdown must release the startup gate and confirmation");
            Require(Exit(() => Task.FromResult(true), () => exits++).IsCompletedSuccessfully && exits == 2
                && (bool)shutdownPending.GetValue(null)!, "a failed exit must permit another exit request");
            Console.WriteLine($"PCR_SHUTDOWN_CHECKS={checks}; synthetic owner, no process exit or device input");
        }
        finally
        {
            current.SetValue(null, previousRun);
            updatePending.SetValue(null, previousUpdate);
            shutdownPending.SetValue(null, previousShutdown);
            if (hadRoot) cache[typeof(RootViewModel)] = oldRoot!; else cache.TryRemove(typeof(RootViewModel), out _);
            SynchronizationContext.SetSynchronizationContext(previousContext);
        }
    }
}

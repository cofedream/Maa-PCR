using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Templates;
using Avalonia.Input;
using Avalonia.Input.Raw;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using Pointer = Avalonia.Input.Pointer;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;

internal static class ModuleOrderChecks
{
    private static readonly string[] Initial = { "deep", "hard", "normal", "event" };
    private static int checks;

    internal static void Run(TaskQueueViewModel vm, string fixtureDirectory)
    {
        SukiUI.SukiTheme.GetInstance().ChangeColorTheme(SukiUI.Enums.SukiColor.Blue);
        var evidence = Path.Combine(fixtureDirectory, "module-order");
        Directory.CreateDirectory(evidence);
        using (var f = new Fixture(vm))
        {
            f.Capture(Path.Combine(evidence, "before.png"));
            var origin = f.Center(0);
            f.Press(0);
            f.Move(origin + new Vector(3, 2));
            Require(!f.IsDragging && f.Saves == 0, "below_threshold_is_click_preview");
            f.Move(origin + new Vector(0, 8));
            typeof(PcrModuleOrderList).GetField("animateDrag", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(f.List, true);
            f.Move(origin + new Vector(12, f.RowHeight * 2.2));
            Require(f.IsDragging && f.Order.SequenceEqual(Initial) && f.Saves == 0, "preview_does_not_reorder_or_save");
            Require(Math.Abs(((TranslateTransform)f.Row(0).RenderTransform!).Y - f.RowHeight * 2.2) < 0.1,
                "dragged_row_follows_pointer_without_transition");
            Pump(90);
            Require(((TranslateTransform)f.Row(1).RenderTransform!).Y < 0, "crossed_rows_move_into_gap");
            f.Capture(Path.Combine(evidence, "dragging.png"));
            var insertion = f.Insertion(f.Center(2));
            Pump(90);
            Require(f.Insertion(f.Center(2)) == insertion, "hit_testing_ignores_preview_animation");
            f.Release(origin + new Vector(12, f.RowHeight * 2.2));
            Require(f.Order.SequenceEqual(new[] { "hard", "normal", "deep", "event" }) && f.Saves == 1 && f.Updates == 1,
                "downward_cross_row_drop_commits_once");
            f.Capture(Path.Combine(evidence, "landing.png"));
            Require(f.HasLandingAnimation && f.Pointer.Captured == null, "second_drag_interrupts_active_landing_animation");
            f.Press(2); // Interrupt the landing transition with a second drag.
            f.Move(new Point(f.Center(2).X, 1));
            f.Release(new Point(f.Center(2).X, 1));
            Require(f.Order.SequenceEqual(Initial) && f.Saves == 2, "continuous_upward_drag_has_one_more_commit");
            Pump(250);
            f.RequireReset("completed_drop");
            f.Capture(Path.Combine(evidence, "after.png"));
        }
        foreach (var cancel in new[] { "escape", "capture", "outside", "hidden", "parent_hidden", "disabled", "parent_disabled", "run_lock", "detach", "layout" })
        {
            using var f = new Fixture(vm);
            f.Press(0); f.Move(f.Center(2));
            switch (cancel)
            {
                case "escape": f.List.RaiseEvent(new KeyEventArgs { RoutedEvent = InputElement.KeyDownEvent, Key = Key.Escape }); break;
                case "capture": f.Pointer.Capture(null); break;
                case "outside": f.Release(new Point(-20, -20)); break;
                case "hidden": f.List.IsVisible = false; break;
                case "parent_hidden": f.Parent.IsVisible = false; break;
                case "disabled": f.List.IsEnabled = false; break;
                case "parent_disabled": f.Parent.IsEnabled = false; break;
                case "run_lock":
                    typeof(TaskQueueViewModel).GetField("_isRunning", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(vm, true);
                    typeof(TaskQueueViewModel).GetMethod("NotifyPcrDiagnosticBoundary", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(vm, null);
                    Dispatcher.UIThread.RunJobs();
                    break;
                case "detach":
                    f.Window.Content = null; f.Layout();
                    Require(f.List.GetVisualRoot() == null, "fixture_detaches_visual_tree");
                    break;
                case "layout": f.Parent.Width = 110; f.Layout(); break;
            }
            // Avalonia can retarget capture to a surviving presenter when its child detaches.
            var captureReleased = cancel == "detach" ? !ReferenceEquals(f.Pointer.Captured, f.List) : f.Pointer.Captured == null;
            Require(f.Order.SequenceEqual(Initial) && f.Saves == 0 && captureReleased,
                cancel + "_cancels_without_commit: order=" + string.Join(",", f.Order) + "; saves=" + f.Saves
                + "; captured=" + f.Pointer.Captured?.GetType().Name + "; dragging=" + f.IsDragging);
            f.RequireReset(cancel);
            typeof(TaskQueueViewModel).GetField("_isRunning", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(vm, false);
            typeof(TaskQueueViewModel).GetMethod("NotifyPcrDiagnosticBoundary", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(vm, null);
        }
        using (var f = new Fixture(vm, 110))
        {
            f.Press(0); f.Move(f.Center(1));
            var scroll = f.List.GetVisualDescendants().OfType<ScrollViewer>().First();
            scroll.Offset = new Vector(0, 30); f.Layout();
            Require(scroll.Offset.Y > 0, "fixture_actually_scrolls");
            Require(f.Pointer.Captured == null && f.Saves == 0 && f.Order.SequenceEqual(Initial), "scroll_cancels_without_commit");
            f.RequireReset("scroll");
        }
        using (var f = new Fixture(vm))
        {
            f.Press(1); f.Release(f.Center(1));
            Require(f.Edited == "hard" && f.Saves == 0, "non_checkbox_click_edits_module_without_saving");
            var check = f.Row(1).GetVisualDescendants().OfType<CheckBox>().Single();
            check.IsChecked = false;
            Require(f.Selection.SelectedCases!.SequenceEqual(new[] { "deep", "normal", "event" }) && f.Saves == 1,
                "checkbox_keeps_checked_subsequence_semantics");
            f.Press(0); f.Move(f.Center(0) + new Vector(0, 8));
            typeof(PcrModuleOrderList).GetField("animateDrag", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(f.List, false);
            f.Move(f.Center(1) + new Vector(0, 8));
            Require(((TranslateTransform)f.Row(1).RenderTransform!).Y == -f.RowHeight,
                "reduced_motion_keeps_immediate_gap_feedback");
            f.Release(f.Center(1) + new Vector(0, 8));
            f.Press(1); f.Release(f.Center(1));
            Require(f.Order.SequenceEqual(new[] { "hard", "deep", "normal", "event" }) && f.Saves == 1 && f.Edited == "deep",
                "unchecked_position_move_does_not_resave_unchanged_selection");
            f.RequireReset("reduced_motion_drop");
        }
        Console.WriteLine($"PCR module drag behavior checks passed: {checks}; screenshots={evidence}; controller/agent starts=0");
    }

    private static void Require(bool value, string name)
    {
        if (!value) throw new InvalidOperationException("module_drag_" + name);
        checks++;
    }

    private static void Pump(int milliseconds)
    {
        var timer = Stopwatch.StartNew();
        do { Dispatcher.UIThread.RunJobs(); Thread.Sleep(10); } while (timer.ElapsedMilliseconds < milliseconds);
        Dispatcher.UIThread.RunJobs();
    }

    private sealed class Fixture : IDisposable
    {
        internal readonly PcrModuleOrderList List;
        internal readonly MaaInterface.MaaInterfaceSelectOption Selection;
        internal readonly Border Parent;
        internal readonly Window Window;
        internal readonly Pointer Pointer = new(100, PointerType.Mouse, true);
        internal int Saves, Updates;
        internal string? Edited;
        internal IEnumerable<string> Order => List.OrderedModules.Select(row => row.Name!);
        internal bool IsDragging => (bool)typeof(PcrModuleOrderList).GetField("dragging", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(List)!;
        internal bool HasLandingAnimation => !IsDragging
            && ((IList)typeof(PcrModuleOrderList).GetField("dragRows", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(List)!)
                .Cast<object>().Any(row => row.GetType().GetField("Animation")!.GetValue(row) is CancellationTokenSource);
        internal double RowHeight => Row(0).Bounds.Height;
        private ulong timestamp;

        internal Fixture(TaskQueueViewModel vm, int height = 300)
        {
            Selection = new MaaInterface.MaaInterfaceSelectOption { Name = "AlphaModules", SelectedCases = Initial.ToList() };
            var definition = new MaaInterface.MaaInterfaceOption {
                Cases = Initial.Select(id => new MaaInterface.MaaInterfaceOptionCase { Name = id }).ToList() };
            List = new PcrModuleOrderList(vm, Selection, definition, () => Saves++, () => Updates++, id => Edited = id);
            if (height < 300) List.ItemsPanel = new FuncTemplate<Panel?>(() => new StackPanel());
            Parent = new Border { Padding = new Thickness(8), Child = List, Background = Brushes.White };
            Window = new Window { Width = 360, Height = height, ShowActivated = false, ShowInTaskbar = false,
                WindowStartupLocation = WindowStartupLocation.Manual, Position = new PixelPoint(-32000, -32000),
                RequestedThemeVariant = ThemeVariant.Light, Content = Parent };
            Window.Show(); Dispatcher.UIThread.RunJobs();
            Window.UpdateLayout();
            Require(List.GetRealizedContainers().Count() == Initial.Length && RowHeight >= 36, "fixture_realizes_all_rows");
            Require(List.GetVisualDescendants().OfType<CheckBox>().Count(check => check.IsChecked == true) == Initial.Length,
                "fixture_preserves_all_checked_modules");
            Saves = Updates = 0;
        }

        internal ListBoxItem Row(int index) => (ListBoxItem)List.ContainerFromIndex(index)!;
        internal void Layout() { Dispatcher.UIThread.RunJobs(); Window.UpdateLayout(); Dispatcher.UIThread.RunJobs(); }
        internal Point Center(int index)
        {
            var row = Row(index);
            var top = row.GetVisualParent()!.TranslatePoint(row.Bounds.Position, List)!.Value;
            return new Point(List.Bounds.Width / 2, top.Y + row.Bounds.Height / 2);
        }
        private Point RootPosition(Point point) => List.TranslatePoint(point, Window)!.Value;
        internal void Press(int index)
        {
            Window.UpdateLayout(); // Synthetic input needs the layout a real hit test would use after collection moves.
            var row = Row(index);
            row.RaiseEvent(new PointerPressedEventArgs(row, Pointer, Window, RootPosition(Center(index)), ++timestamp,
                new PointerPointProperties(RawInputModifiers.LeftMouseButton, PointerUpdateKind.LeftButtonPressed), KeyModifiers.None, 1));
        }
        internal void Move(Point point) => List.RaiseEvent(new PointerEventArgs(InputElement.PointerMovedEvent, List,
            Pointer, Window, RootPosition(point), ++timestamp,
            new PointerPointProperties(RawInputModifiers.LeftMouseButton, PointerUpdateKind.Other), KeyModifiers.None));
        internal void Release(Point point) => List.RaiseEvent(new PointerReleasedEventArgs(List, Pointer, Window,
            RootPosition(point), ++timestamp, new PointerPointProperties(RawInputModifiers.None, PointerUpdateKind.LeftButtonReleased),
            KeyModifiers.None, MouseButton.Left));
        internal int? Insertion(Point point) => (int?)typeof(PcrModuleOrderList).GetMethod("InsertionIndex", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(List, new object[] { point });
        internal void RequireReset(string boundary)
        {
            Require(!IsDragging && ((IList)typeof(PcrModuleOrderList).GetField("dragRows", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(List)!).Count == 0,
                boundary + "_clears_drag_state");
            foreach (var row in List.GetRealizedContainers().OfType<ListBoxItem>())
                Require(row.RenderTransform == null && row.Opacity == 1 && row.ZIndex == 0, boundary + "_restores_row_visuals");
        }
        internal void Capture(string path)
        {
            using var bitmap = new RenderTargetBitmap(new PixelSize(360, 300), new Vector(96, 96));
            bitmap.Render(Parent); bitmap.Save(path);
        }
        public void Dispose() { Pointer.Capture(null); Pointer.Dispose(); Window.Close(); Dispatcher.UIThread.RunJobs(); }
    }
}

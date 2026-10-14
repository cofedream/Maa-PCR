using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Animation;
using Avalonia.Animation.Easings;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Controls.Templates;
using Avalonia.Data;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Styling;
using Avalonia.VisualTree;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// SelectedCases is the checked subsequence of this one visible module list.
public sealed class PcrModuleOrderList : ListBox
{
    protected override Type StyleKeyOverride => typeof(ListBox);
    private readonly MaaInterface.MaaInterfaceSelectOption option;
    private readonly TaskQueueViewModel vm;
    private readonly Action save;
    private readonly Action selectionChanged;
    private readonly HashSet<string> selected = new();
    private IPointer? pressedPointer;
    private Point pressedPosition;
    private int pressedIndex = -1;
    private ListBoxItem? pressedRow;
    private bool dragging;
    private bool animateDrag;
    private Size dragBounds;
    private readonly List<DragRow> dragRows = new();
    private readonly List<Visual> visibilityParents = new();
    private sealed class DragRow
    {
        public required ListBoxItem Row;
        public required MaaInterface.MaaInterfaceOptionCase Module;
        public required Rect Bounds;
        public required ITransform? OriginalTransform;
        public required double OriginalOpacity;
        public required int OriginalZIndex;
        public readonly TranslateTransform Transform = new();
        public CancellationTokenSource? Animation;
        public double TargetX, TargetY;
    }
    public ObservableCollection<MaaInterface.MaaInterfaceOptionCase> OrderedModules { get; } = new();

    public PcrModuleOrderList(TaskQueueViewModel viewModel, MaaInterface.MaaInterfaceSelectOption selection,
        MaaInterface.MaaInterfaceOption definition, Action persist, Action updateSubOptions,
        Action<string>? editModule = null, string? activeModule = null)
    {
        vm = viewModel; option = selection; save = persist; selectionChanged = updateSubOptions;
        option.SelectedCases ??= new List<string>(definition.DefaultCases ?? new List<string>());
        Name = "PcrModuleOrderList";
        Classes.Add("Fluent");
        Styles.Add(new Style(x => x.OfType<ListBoxItem>()) { Setters = {
            new Setter(PaddingProperty, new Thickness(6, 0)), new Setter(MinHeightProperty, 36d) } });
        HorizontalAlignment = HorizontalAlignment.Stretch;
        ItemsSource = OrderedModules;
        AutomationProperties.SetName(this, "勾选任务模块，拖动左侧拖柄调整执行顺序");
        foreach (var row in definition.Cases ?? new List<MaaInterface.MaaInterfaceOptionCase>()) row.InitializeDisplayName();
        foreach (var id in option.SelectedCases ?? new List<string>()) selected.Add(id);
        var cases = definition.Cases ?? new List<MaaInterface.MaaInterfaceOptionCase>();
        foreach (var id in option.SelectedCases ?? new List<string>())
        {
            var row = cases.SingleOrDefault(item => item.Name == id);
            if (row != null && !OrderedModules.Contains(row)) OrderedModules.Add(row);
        }
        foreach (var row in cases) if (!OrderedModules.Contains(row)) OrderedModules.Add(row);
        ItemTemplate = new FuncDataTemplate<MaaInterface.MaaInterfaceOptionCase>((row, _) =>
        {
            if (row == null) return new Border(); // container recycling clears its content
            var panel = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,24,*"),
                Margin = new Thickness(2, 2), MinHeight = 32, Background = Avalonia.Media.Brushes.Transparent };
            var check = new CheckBox { IsChecked = selected.Contains(row.Name!), VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 4, 0) };
            AutomationProperties.SetName(check, "勾选" + row.DisplayName);
            check.IsCheckedChanged += (_, _) =>
            {
                if (!vm.CanEditPcrTarget) return;
                CancelDrag();
                if (check.IsChecked == true) selected.Add(row.Name!); else selected.Remove(row.Name!);
                PersistSelection();
            };
            var handle = new TextBlock { Text = "≡", FontSize = 20, MinWidth = 24,
                VerticalAlignment = VerticalAlignment.Center, Cursor = new Cursor(StandardCursorType.SizeAll) };
            ToolTip.SetTip(handle, "按住拖动，松开后调整顺序");
            Grid.SetColumn(handle, 1);
            var label = new TextBlock { VerticalAlignment = VerticalAlignment.Center, TextWrapping = Avalonia.Media.TextWrapping.Wrap };
            label.Text = TaskQueueViewModel.PcrModuleName(row.Name ?? "");
            Grid.SetColumn(label, 2);
            if (row.HasDescription) ToolTip.SetTip(panel, row.DisplayDescription);
            panel.Children.Add(check); panel.Children.Add(handle); panel.Children.Add(label);
            return panel;
        });
        OrderedModules.CollectionChanged += (_, _) => PersistSelection();
        this.Bind(IsEnabledProperty, new Binding(nameof(TaskQueueViewModel.CanEditPcrTarget)) { Source = vm });
        AddHandler(PointerPressedEvent, Pressed, RoutingStrategies.Tunnel, handledEventsToo: true);
        AddHandler(PointerMovedEvent, Moved, RoutingStrategies.Tunnel, handledEventsToo: true);
        AddHandler(PointerReleasedEvent, Released, RoutingStrategies.Tunnel, handledEventsToo: true);
        AddHandler(PointerCaptureLostEvent, (_, _) => CancelDrag(), RoutingStrategies.Direct, handledEventsToo: true);
        AddHandler(KeyDownEvent, (_, e) => { if (e.Key == Key.Escape && pressedPointer != null) { CancelDrag(); e.Handled = true; } });
        LayoutUpdated += (_, _) =>
        {
            if (pressedPointer == null || !dragging) return;
            if (Bounds.Size != dragBounds || dragRows.Any(visual => visual.Row.Bounds.Size != visual.Bounds.Size
                || visual.Row.GetVisualParent()?.TranslatePoint(visual.Row.Bounds.Position, this) != visual.Bounds.Position)) CancelDrag();
        };
        AttachedToVisualTree += (_, _) =>
        {
            foreach (var parent in this.GetVisualAncestors()) { visibilityParents.Add(parent); parent.PropertyChanged += AncestorChanged; }
        };
        DetachedFromVisualTree += (_, _) =>
        {
            foreach (var parent in visibilityParents) parent.PropertyChanged -= AncestorChanged;
            visibilityParents.Clear(); CancelDrag();
        };
        SelectionChanged += (_, _) =>
        {
            if (SelectedItem is MaaInterface.MaaInterfaceOptionCase { Name: { } id }) editModule?.Invoke(id);
        };
        SelectedItem = OrderedModules.FirstOrDefault(row => row.Name == activeModule) ?? OrderedModules.FirstOrDefault();
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == IsEffectivelyEnabledProperty && !IsEffectivelyEnabled
            || change.Property == IsVisibleProperty && !IsVisible) CancelDrag();
    }

    private void AncestorChanged(object? sender, AvaloniaPropertyChangedEventArgs change)
    {
        if (change.Property == IsVisibleProperty && sender is Visual { IsVisible: false }) CancelDrag();
    }

    private void PersistSelection()
    {
        var order = OrderedModules.Where(row => selected.Contains(row.Name!)).Select(row => row.Name!).ToList();
        if (!vm.CanEditPcrTarget || (option.SelectedCases ?? new List<string>()).SequenceEqual(order)) return;
        option.SelectedCases = order;
        PcrAlphaOwner.Revoke(vm.Processor);
        selectionChanged();
        save();
    }

    private void Pressed(object? sender, PointerPressedEventArgs e)
    {
        if (!vm.CanEditPcrTarget || !IsEffectivelyEnabled || !e.GetCurrentPoint(this).Properties.IsLeftButtonPressed || e.Source is not Visual source) return;
        var ancestors = source.GetVisualAncestors().ToArray();
        if (source is CheckBox || ancestors.Any(item => item is CheckBox)) return;
        var row = source as ListBoxItem ?? ancestors.OfType<ListBoxItem>().FirstOrDefault();
        if (row == null || IndexFromContainer(row) < 0) return;
        CancelDrag();
        pressedIndex = IndexFromContainer(row); pressedRow = row; pressedPosition = e.GetPosition(this); pressedPointer = e.Pointer;
        e.Pointer.Capture(this); Focus(); e.Handled = true;
    }

    private void Moved(object? sender, PointerEventArgs e)
    {
        if (pressedPointer != e.Pointer) return;
        if (!vm.CanEditPcrTarget || !IsEffectivelyEnabled || !IsEffectivelyVisible
            || !e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) { CancelDrag(); return; }
        var point = e.GetPosition(this); var dx = point.X - pressedPosition.X; var dy = point.Y - pressedPosition.Y;
        if (!dragging && dx * dx + dy * dy >= 36)
        {
            dragging = true;
            animateDrag = ClientAnimationsEnabled();
            CaptureDragRows();
            if (pressedRow != null) { pressedRow.Opacity *= 0.75; pressedRow.ZIndex = dragRows.Max(row => row.OriginalZIndex) + 1; }
        }
        if (dragging)
        {
            var insertion = InsertionIndex(point);
            var target = insertion > pressedIndex ? insertion - 1 : insertion;
            foreach (var visual in dragRows)
            {
                var index = OrderedModules.IndexOf(visual.Module);
                if (index == pressedIndex) _ = SetPosition(visual, dx, dy, false);
                else
                {
                    var shift = target < pressedIndex && index >= target && index < pressedIndex ? pressedRow!.Bounds.Height
                        : target > pressedIndex && index <= target && index > pressedIndex ? -pressedRow!.Bounds.Height : 0;
                    _ = SetPosition(visual, 0, shift, animateDrag);
                }
            }
        }
        e.Handled = true;
    }

    private void CaptureDragRows()
    {
        dragBounds = Bounds.Size;
        for (var i = 0; i < OrderedModules.Count; i++)
        {
            if (ContainerFromIndex(i) is not ListBoxItem row || !row.IsEffectivelyVisible
                || row.GetVisualParent()?.TranslatePoint(row.Bounds.Position, this) is not { } top) continue;
            var visual = new DragRow { Row = row, Module = OrderedModules[i], Bounds = new Rect(top, row.Bounds.Size),
                OriginalTransform = row.RenderTransform, OriginalOpacity = row.Opacity, OriginalZIndex = row.ZIndex };
            dragRows.Add(visual);
            row.RenderTransform = visual.Transform;
        }
    }

    private async Task SetPosition(DragRow visual, double x, double y, bool animate)
    {
        if (Math.Abs(visual.TargetX - x) < 0.1 && Math.Abs(visual.TargetY - y) < 0.1) return;
        var startX = visual.Transform.X; var startY = visual.Transform.Y;
        visual.Animation?.Cancel(); visual.Animation?.Dispose(); visual.Animation = null;
        visual.TargetX = x; visual.TargetY = y;
        visual.Transform.X = x; visual.Transform.Y = y;
        if (!animate) return;
        var cancel = visual.Animation = new CancellationTokenSource();
        var animation = new Animation { Duration = TimeSpan.FromMilliseconds(160), Easing = new CubicEaseOut(),
            Children = {
                new KeyFrame { Cue = new Cue(0), Setters = {
                    new Setter(TranslateTransform.XProperty, startX), new Setter(TranslateTransform.YProperty, startY) } },
                new KeyFrame { Cue = new Cue(1), Setters = {
                    new Setter(TranslateTransform.XProperty, x), new Setter(TranslateTransform.YProperty, y) } } } };
        try { await animation.RunAsync(visual.Row, cancel.Token); }
        catch (OperationCanceledException) { }
        finally
        {
            if (visual.Animation == cancel) { visual.Animation = null; cancel.Dispose(); }
        }
    }

    private int? InsertionIndex(Point point)
    {
        if (!new Rect(Bounds.Size).Contains(point)) return null;
        int? last = null;
        if (dragging)
        {
            // Hit testing must use the layout before preview transforms move the rows.
            foreach (var visual in dragRows)
            {
                var index = OrderedModules.IndexOf(visual.Module);
                if (point.Y < visual.Bounds.Center.Y) return index;
                last = index + 1;
            }
            return last;
        }
        for (var i = 0; i < OrderedModules.Count; i++)
        {
            if (ContainerFromIndex(i) is not ListBoxItem row || !row.IsEffectivelyVisible) continue;
            var top = row.TranslatePoint(new Point(0, 0), this);
            if (top == null) continue;
            if (point.Y < top.Value.Y + row.Bounds.Height / 2) return i;
            last = i + 1;
        }
        return last;
    }

    private void Released(object? sender, PointerReleasedEventArgs e)
    {
        if (pressedPointer != e.Pointer) return;
        var from = pressedIndex;
        var editable = vm.CanEditPcrTarget && IsEffectivelyEnabled && IsEffectivelyVisible;
        var isClick = !dragging && editable && pressedRow != null
            && new Rect(pressedRow.Bounds.Size).Contains(e.GetPosition(pressedRow));
        var target = dragging && editable ? InsertionIndex(e.GetPosition(this)) : null;
        var positions = dragRows.ToDictionary(item => item.Module,
            item => item.Bounds.Position + new Vector(item.Transform.X, item.Transform.Y));
        var animate = animateDrag;
        CancelDrag(); e.Handled = true;
        if (isClick && from >= 0 && from < OrderedModules.Count) SelectedItem = OrderedModules[from];
        if (target == null || from < 0 || from >= OrderedModules.Count) return;
        var to = target.Value > from ? target.Value - 1 : target.Value;
        if (to >= 0 && to < OrderedModules.Count)
        {
            if (to != from) OrderedModules.Move(from, to);
            if (animate && IsEffectivelyVisible && IsEffectivelyEnabled)
            {
                UpdateLayout();
                CaptureDragRows();
                var movements = new List<Task>();
                foreach (var visual in dragRows)
                {
                    if (!positions.TryGetValue(visual.Module, out var before)) continue;
                    _ = SetPosition(visual, before.X - visual.Bounds.X, before.Y - visual.Bounds.Y, false);
                    movements.Add(SetPosition(visual, 0, 0, true));
                }
                FinishDrop(dragRows.ToArray(), movements);
            }
        }
    }

    private async void FinishDrop(DragRow[] settlingRows, List<Task> movements)
    {
        await Task.WhenAll(movements);
        if (settlingRows.Length > 0 && dragRows.SequenceEqual(settlingRows)) ResetDragRows();
    }

    private void ResetDragRows()
    {
        var restoringRows = dragRows.ToArray();
        dragRows.Clear();
        foreach (var visual in restoringRows)
        {
            var cancel = visual.Animation;
            visual.Animation = null;
            cancel?.Cancel(); cancel?.Dispose();
            visual.Row.RenderTransform = visual.OriginalTransform;
            visual.Row.Opacity = visual.OriginalOpacity;
            visual.Row.ZIndex = visual.OriginalZIndex;
        }
    }

    private void CancelDrag()
    {
        var pointer = pressedPointer;
        pressedPointer = null; pressedIndex = -1; dragging = false;
        ResetDragRows();
        pressedRow = null;
        if (pointer?.Captured == this) pointer.Capture(null);
    }

    private static bool ClientAnimationsEnabled() => !OperatingSystem.IsWindows()
        || !SystemParametersInfo(0x1042, 0, out var enabled, 0) || enabled != 0;

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SystemParametersInfo(uint action, uint parameter, out int value, uint flags);

    public static string ApplySelectionOrder(string entry, string parameters, IReadOnlyList<string> selected)
    {
        if (entry != "PCRAlphaEntry") return parameters;
        var parsed = JToken.Parse(parameters);
        var layers = parsed is JArray array ? array : new JArray(parsed);
        JObject? custom = null;
        foreach (var layer in layers)
            if (layer[entry]?["custom_action_param"] is JObject value) custom = (JObject)value.DeepClone();
        if (custom == null || (string?)custom["selection_mode"] != "pi_nodes_v1")
            throw new InvalidOperationException("PCR 组合任务参数无效");
        custom["selected_modules"] = new JArray(selected);
        layers.Add(new JObject { [entry] = new JObject { ["custom_action_param"] = custom } });
        var result = layers.ToString(Formatting.None);
        _ = PcrAlphaOwner.Selection(entry, result); // same legality and selected-node set contract
        return result;
    }
}

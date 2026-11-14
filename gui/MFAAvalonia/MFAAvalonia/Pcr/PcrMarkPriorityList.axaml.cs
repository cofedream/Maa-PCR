using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Data;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Layout;
using Avalonia.Markup.Xaml;
using Avalonia.Media;
using Avalonia.VisualTree;

namespace MFAAvalonia.Pcr;

/// <summary>
/// Five fixed relic-mark blocks whose order is the custom priority. Dragging only
/// reorders the blocks; the checkbox gates the whole custom order. The bound string
/// stays the single persisted value: empty (built-in strategy) or the five canonical
/// marks in priority order. A stored ordered subset is expanded by
/// <see cref="Normalize"/> + <see cref="Expand"/> before it reaches this control.
/// </summary>
public partial class PcrMarkPriorityList : UserControl
{
    public static readonly IReadOnlyList<string> Marks = new[] { "加速", "会心", "守备", "强化", "弱体" };

    public static readonly StyledProperty<string> PriorityProperty =
        AvaloniaProperty.Register<PcrMarkPriorityList, string>(nameof(Priority), defaultValue: "",
            defaultBindingMode: BindingMode.TwoWay);

    private readonly List<string> order = new(Marks);
    private readonly List<Border> chips = new();
    private StackPanel chipHost = null!;
    private CheckBox enableToggle = null!;
    private bool syncing;
    private IPointer? pressedPointer;
    private Border? pressedChip;
    private Point pressedPosition;
    private int pressedIndex = -1;
    private bool dragging;
    private TranslateTransform? dragTransform;
    private Rect[] pressedBounds = Array.Empty<Rect>();

    public PcrMarkPriorityList()
    {
        AvaloniaXamlLoader.Load(this);
        chipHost = this.FindControl<StackPanel>("ChipHost")!;
        enableToggle = this.FindControl<CheckBox>("EnableToggle")!;
        enableToggle.IsCheckedChanged += (_, _) => OnToggleChanged();
        ApplyPriority(Priority);
    }

    /// <summary>The persisted priority: empty, or the five canonical marks in order.</summary>
    public string Priority
    {
        get => GetValue(PriorityProperty);
        set => SetValue(PriorityProperty, value);
    }

    /// <summary>Marks in the order currently displayed (test/reporting hook).</summary>
    public IReadOnlyList<string> DisplayOrder => order.ToArray();

    /// <summary>Canonical marks from an untrusted list: aliases mapped, unknowns and duplicates dropped.</summary>
    public static IReadOnlyList<string> Normalize(IEnumerable<string?>? marks)
    {
        var result = new List<string>();
        foreach (var raw in marks ?? Enumerable.Empty<string?>())
        {
            if (raw is null) continue;
            var mark = raw.Trim() switch
            {
                "暴击" => "会心",
                "削弱" or "崩弱" => "弱体",
                var named => named,
            };
            if (Marks.Contains(mark) && !result.Contains(mark)) result.Add(mark);
        }
        return result;
    }

    /// <summary>Keeps the stored relative order and appends the missing marks in built-in order.</summary>
    public static IReadOnlyList<string> Expand(IReadOnlyList<string> marks)
    {
        var result = marks.ToList();
        foreach (var mark in Marks)
            if (!result.Contains(mark)) result.Add(mark);
        return result;
    }

    /// <summary>Moves one block; persists the new order when the custom priority is enabled.</summary>
    public void Move(int from, int to)
    {
        if (from == to || from < 0 || to < 0 || from >= order.Count || to >= order.Count) return;
        var mark = order[from];
        order.RemoveAt(from);
        order.Insert(to, mark);
        if (enableToggle.IsChecked == true) WritePriority(string.Join(",", order));
        else RebuildChips(false);
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == PriorityProperty && !syncing)
            ApplyPriority(change.GetNewValue<string>() ?? "");
    }

    private void ApplyPriority(string? value)
    {
        syncing = true;
        try
        {
            var stored = Normalize(Split(value));
            var enabled = stored.Count > 0;
            if (enabled)
            {
                order.Clear();
                order.AddRange(stored.Count >= Marks.Count ? stored : Expand(stored));
            }
            else if (order.Count != Marks.Count)
            {
                order.Clear();
                order.AddRange(Marks);
            }
            if (enableToggle.IsChecked != enabled) enableToggle.IsChecked = enabled;
            RebuildChips(enabled);
        }
        finally { syncing = false; }
    }

    private void OnToggleChanged()
    {
        if (syncing) return;
        var enabled = enableToggle.IsChecked == true;
        WritePriority(enabled ? string.Join(",", order) : "");
        if (!enabled) RebuildChips(false);
    }

    // SetCurrentValue keeps the installed TwoWay binding; the binding pushes the
    // value to the view model, whose own change notification comes straight back.
    private void WritePriority(string value) => SetCurrentValue(PriorityProperty, value);

    private static IEnumerable<string> Split(string? value) =>
        string.IsNullOrWhiteSpace(value)
            ? Enumerable.Empty<string>()
            : value.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

    private void RebuildChips(bool enabled)
    {
        ResetChipVisuals();
        chips.Clear();
        chipHost.Children.Clear();
        for (var i = 0; i < order.Count; i++)
        {
            var mark = order[i];
            var content = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            content.Children.Add(new TextBlock
            {
                Text = mark,
                FontSize = 13,
                VerticalAlignment = VerticalAlignment.Center,
            });
            if (enabled)
            {
                var badge = new Border { Classes = { "MarkChipBadge" } };
                badge.Child = new TextBlock
                {
                    Text = (i + 1).ToString(CultureInfo.InvariantCulture),
                    FontSize = 10,
                };
                content.Children.Add(badge);
            }
            var chip = new Border { Classes = { "MarkChip" }, Child = content, Tag = mark };
            chip.Classes.Add(enabled ? "enabled" : "disabled");
            AutomationProperties.SetName(chip, enabled ? $"{mark}，优先级第 {i + 1}" : $"{mark}，未启用");
            ToolTip.SetTip(chip, enabled ? "按住拖动调整印记优先顺序" : "勾选「启用自定义」后可拖动调整顺序");
            chip.AddHandler(InputElement.PointerPressedEvent, Pressed, RoutingStrategies.Tunnel);
            chip.AddHandler(InputElement.PointerMovedEvent, Moved, RoutingStrategies.Tunnel);
            chip.AddHandler(InputElement.PointerReleasedEvent, Released, RoutingStrategies.Tunnel);
            chip.AddHandler(InputElement.PointerCaptureLostEvent, (_, _) => CancelDrag(), RoutingStrategies.Direct);
            chipHost.Children.Add(chip);
            chips.Add(chip);
        }
    }

    private void Pressed(object? sender, PointerPressedEventArgs e)
    {
        if (enableToggle.IsChecked != true || !IsEffectivelyEnabled || !IsEffectivelyVisible) return;
        if (sender is not Border chip || pressedPointer is not null) return;
        if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) return;
        CancelDrag();
        pressedPointer = e.Pointer;
        pressedChip = chip;
        pressedIndex = chips.IndexOf(chip);
        pressedPosition = e.GetPosition(chipHost);
        pressedBounds = chips
            .Select(item => new Rect(item.TranslatePoint(new Point(0, 0), chipHost) ?? default, item.Bounds.Size))
            .ToArray();
        e.Pointer.Capture(chip);
        e.Handled = true;
    }

    private void Moved(object? sender, PointerEventArgs e)
    {
        if (pressedPointer != e.Pointer || pressedChip is null) return;
        if (!IsEffectivelyEnabled || !e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) { CancelDrag(); return; }
        var point = e.GetPosition(chipHost);
        var dx = point.X - pressedPosition.X;
        var dy = point.Y - pressedPosition.Y;
        if (!dragging && dx * dx + dy * dy >= 36)
        {
            dragging = true;
            pressedChip.Classes.Add("dragging");
            pressedChip.ZIndex = 100;
            dragTransform = new TranslateTransform();
            pressedChip.RenderTransform = dragTransform;
        }
        if (!dragging) return;
        dragTransform!.X = dx;
        e.Handled = true;
    }

    private void Released(object? sender, PointerReleasedEventArgs e)
    {
        if (pressedPointer != e.Pointer || pressedChip is null) return;
        var from = pressedIndex;
        var wasDragging = dragging;
        var target = wasDragging ? InsertionIndex(e.GetPosition(chipHost)) : -1;
        CancelDrag();
        if (!wasDragging || from < 0 || from >= order.Count || target < 0) return;
        var to = target > from ? target - 1 : target;
        if (to >= 0 && to < order.Count && to != from) Move(from, to);
    }

    private int InsertionIndex(Point point)
    {
        int? last = null;
        for (var i = 0; i < pressedBounds.Length; i++)
        {
            if (point.X < pressedBounds[i].Center.X) return i;
            last = i + 1;
        }
        return last ?? pressedBounds.Length;
    }

    private void CancelDrag()
    {
        var pointer = pressedPointer;
        pressedPointer = null;
        pressedChip = null;
        pressedIndex = -1;
        dragging = false;
        dragTransform = null;
        pressedBounds = Array.Empty<Rect>();
        ResetChipVisuals();
        if (pointer is not null && pointer.Captured is InputElement captured && chips.Contains(captured))
            pointer.Capture(null);
    }

    private void ResetChipVisuals()
    {
        foreach (var chip in chips)
        {
            chip.ZIndex = 0;
            chip.RenderTransform = null;
            chip.Classes.Remove("dragging");
        }
    }
}

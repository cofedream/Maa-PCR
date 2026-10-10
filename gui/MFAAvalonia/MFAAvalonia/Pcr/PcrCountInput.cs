using System;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;

namespace MFAAvalonia.Pcr;

// Keep the saved draft separate from NumericUpDown's initialization of Text
// from its initially null Value. Connect it only after that synchronous step.
public class PcrCountInput : NumericUpDown
{
    public static readonly StyledProperty<string?> DraftTextProperty =
        AvaloniaProperty.Register<PcrCountInput, string?>(nameof(DraftText));

    public string? DraftText
    {
        get => GetValue(DraftTextProperty);
        set => SetValue(DraftTextProperty, value);
    }

    protected override Type StyleKeyOverride => typeof(NumericUpDown);
    private bool initialized;
    private bool syncing;

    public PcrCountInput()
    {
        Initialized += (_, _) => {
            initialized = true;
            RestoreDraft();
        };
    }

    private void RestoreDraft()
    {
        syncing = true;
        try { SetCurrentValue(TextProperty, DraftText); }
        finally { syncing = false; }
    }

    protected override void OnLostFocus(RoutedEventArgs e)
    {
        // NumericUpDown formats Value on blur, which otherwise replaces an
        // invalid/empty draft with the last numeric value. Keep its focus and
        // validation handling, but let Start validate the original draft.
        syncing = true;
        try { base.OnLostFocus(e); }
        finally { syncing = false; RestoreDraft(); }
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (!initialized || syncing) return;
        if (change.Property == DraftTextProperty) RestoreDraft();
        else if (change.Property == TextProperty) SetCurrentValue(DraftTextProperty, Text);
    }
}

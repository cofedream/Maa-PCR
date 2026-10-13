using System;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class AlphaOwnerChecks
{
    internal static void Run()
    {
        var count = 0;
        void Require(bool value, string check)
        {
            if (!value) throw new InvalidOperationException(check);
            count++;
        }
        void Rejected(Action action, string check)
        {
            try { action(); }
            catch (Exception error) when (error.GetBaseException() is InvalidOperationException)
            { count++; return; }
            throw new InvalidOperationException(check);
        }

        const BindingFlags hidden = BindingFlags.NonPublic | BindingFlags.Static;
        var validate = typeof(PcrStartConsent).GetMethod("Validate", hidden)!;
        var schedule = JObject.Parse("{\"selected_modules\":[\"schedule_onekey\"]}");
        var rewards = JObject.Parse("{\"selected_modules\":[\"rewards\"]}");
        var sweep = JObject.Parse("{\"selected_modules\":[\"event\"]}");
        var config = JObject.Parse("{\"version\":3,\"presets\":[]}");
        var gui = new PcrStartConsent("", "gui-current-state", ["arena_coins"], false, true);
        PcrStartConsent Validate(JObject selection, PcrStartConsent consent)
            => (PcrStartConsent)validate.Invoke(null, [selection, config, consent])!;
        Require(Validate(schedule, gui) == gui, "gui_schedule_preserves_current_state_and_explicit_recovery");
        config["version"] = 4;
        Require(Validate(schedule, gui) == gui, "v4_gui_schedule_accepted");
        config["version"] = 2;
        Rejected(() => Validate(schedule, gui), "legacy_config_cannot_assert_gui_current_state");
        config["version"] = 3;
        Rejected(() => Validate(schedule, gui with { GameDay = "2026-10-13" }), "gui_cannot_supply_its_own_day");
        var cli = gui with { GameDay = "2026-10-13", CountContext = "user-reported-unreset", AttestCurrentPlan = true, ConfirmNewCycle = false };
        Require(Validate(schedule, cli).CountContext == "", "schedule_only_cli_does_not_invent_counter_assertion");
        Rejected(() => Validate(schedule, cli with { AttestCurrentPlan = false }), "cli_schedule_requires_plan_attestation");
        Rejected(() => Validate(schedule, cli with { GameDay = "bad-date" }), "cli_schedule_requires_valid_day");
        Rejected(() => Validate(schedule, cli with { ConfirmNewCycle = true }), "cli_cannot_confirm_gui_recovery");
        foreach (var authorizations in new string[][] { [], ["arena_coins", "arena_coins"], ["unknown_item"], null! })
            Rejected(() => Validate(schedule, gui with { ScheduleAuthorizations = authorizations }), "invalid_schedule_authorizations_rejected");
        var empty = new PcrStartConsent("2026-10-13", "user-reported-unreset", [], false);
        Require(Validate(rewards, empty) == empty with { GameDay = "", CountContext = "" }, "reward_only_removes_irrelevant_assertions");
        Rejected(() => Validate(rewards, gui with { ConfirmNewCycle = false }), "non_schedule_cannot_carry_authorization");
        Rejected(() => Validate(rewards, empty with { AttestCurrentPlan = true }), "non_schedule_cannot_attest_plan");
        Rejected(() => Validate(rewards, empty with { ConfirmNewCycle = true }), "non_schedule_cannot_confirm_recovery");
        Require(Validate(sweep, empty) == empty, "sweep_preserves_explicit_day_and_counter_context");
        Rejected(() => Validate(sweep, empty with { CountContext = "" }), "sweep_requires_counter_context");
        var guiSweep = gui with { ScheduleAuthorizations = [], ConfirmNewCycle = false };
        Require(Validate(sweep, guiSweep) == guiSweep, "gui_sweep_uses_fresh_state");

        var owner = typeof(PcrAlphaOwner);
        var current = owner.GetField("current", hidden)!;
        var pending = owner.GetField("pendingStart", hidden)!;
        var cleanup = owner.GetField("cleanupRevoke", hidden)!;
        Require(current.GetValue(null) == null && pending.GetValue(null) == null, "fixture_requires_idle_owner");
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        var other = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        var sessionType = owner.GetNestedType("Session", BindingFlags.NonPublic)!;
        var configType = owner.GetNestedType("ValidatedConfig", BindingFlags.NonPublic)!;
        object Construct(Type type, params object?[] values)
            => Activator.CreateInstance(type, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic, null, values, null)!;
        object Field(object value, string name) => value.GetType().GetField(name)!.GetValue(value)!;
        void Set(object value, string name, object content) => value.GetType().GetField(name)!.SetValue(value, content);
        var oldBuilder = owner.GetProperty(nameof(PcrAlphaOwner.ControllerBuilder))!.GetValue(null);
        var oldScript = owner.GetProperty(nameof(PcrAlphaOwner.OfflineAgentScript))!.GetValue(null);
        var oldMuMu = owner.GetProperty(nameof(PcrAlphaOwner.OfflineMuMuFactory))!.GetValue(null);
        Func<JObject, MaaController> neverAllocate = _ => throw new InvalidOperationException("fixture_must_never_allocate_controller");
        object? session = null;
        object? attempt = null;
        try
        {
            PcrAlphaOwner.ControllerBuilder = neverAllocate;
            PcrAlphaOwner.OfflineMuMuFactory = null;
            PcrAlphaOwner.OfflineAgentScript = "inert-offline-fixture";
            session = Construct(sessionType, processor, schedule, Construct(configType, "{}"), gui);
            current.SetValue(null, session);
            PcrAlphaOwner.ControllerBuilder = null;
            PcrAlphaOwner.OfflineAgentScript = null;
            Require((bool)sessionType.GetProperty("IsOffline")!.GetValue(session)!
                && ReferenceEquals(Field(session, "OfflineControllerBuilder"), neverAllocate), "session_captures_construction_seam_once");
            Require(!ReferenceEquals(Field(session, "Selection"), schedule)
                && JToken.DeepEquals((JObject)Field(session, "Selection"), schedule), "session_owns_selection_snapshot");
            Require(!PcrAlphaOwner.ClaimStart(other), "other_processor_cannot_claim_start");
            Set(session, "Preparing", true);
            Require(!PcrAlphaOwner.ClaimStart(processor), "preparing_cannot_start");
            Set(session, "Preparing", false);
            sessionType.GetProperty("PreparationOnly")!.SetValue(session, true);
            Require(!PcrAlphaOwner.ClaimStart(processor), "read_only_preparation_cannot_start");
            sessionType.GetProperty("PreparationOnly")!.SetValue(session, false);
            PcrAlphaOwner.Require(processor);
            Require(PcrAlphaOwner.ClaimStart(processor) && !PcrAlphaOwner.ClaimStart(processor), "session_start_is_single_use");
            PcrAlphaOwner.Revoke(other);
            Require(!(bool)Field(session, "Revoked"), "other_processor_cannot_revoke_session");
            PcrAlphaOwner.Revoke(processor);
            Require((bool)Field(session, "Revoked") && ((CancellationTokenSource)Field(session, "Cancel")).IsCancellationRequested,
                "stop_revokes_and_cancels_session");
            Require(!PcrAlphaOwner.ClaimStart(processor), "revoked_session_cannot_restart");
            Rejected(() => PcrAlphaOwner.Require(processor), "revoked_session_cannot_authorize_work");
            Set(session, "Preparing", true);
            PcrAlphaOwner.Release(processor);
            Require(ReferenceEquals(current.GetValue(null), session), "inflight_preparation_retains_owner_until_cleanup");
            Set(session, "Preparing", false);
            PcrAlphaOwner.Release(other);
            Require(ReferenceEquals(current.GetValue(null), session), "other_processor_cannot_release_owner");
            PcrAlphaOwner.Release(processor);
            Require(current.GetValue(null) == null && !PcrAlphaOwner.ClaimStart(processor), "released_session_cannot_restart");

            attempt = Construct(owner.GetNestedType("PendingStart", BindingFlags.NonPublic)!, processor);
            pending.SetValue(null, attempt);
            PcrAlphaOwner.Revoke(other);
            Require(!(bool)Field(attempt, "Revoked"), "other_processor_cannot_cancel_pending_start");
            cleanup.SetValue(null, attempt);
            PcrAlphaOwner.Revoke(processor);
            Require(cleanup.GetValue(null) == null && !(bool)Field(attempt, "Revoked"), "tasker_cleanup_consumes_only_its_own_revocation");
            PcrAlphaOwner.Revoke(processor);
            Require((bool)Field(attempt, "Revoked") && ((CancellationTokenSource)Field(attempt, "Cancel")).IsCancellationRequested,
                "subsequent_stop_cancels_pending_start");
            Rejected(() => owner.GetMethod("RequirePendingStart", hidden)!.Invoke(null, [attempt]), "canceled_start_cannot_resume");
        }
        finally
        {
            current.SetValue(null, null);
            pending.SetValue(null, null);
            cleanup.SetValue(null, null);
            if (session != null) ((CancellationTokenSource)Field(session, "Cancel")).Dispose();
            if (attempt != null) ((CancellationTokenSource)Field(attempt, "Cancel")).Dispose();
            owner.GetProperty(nameof(PcrAlphaOwner.ControllerBuilder))!.SetValue(null, oldBuilder);
            owner.GetProperty(nameof(PcrAlphaOwner.OfflineAgentScript))!.SetValue(null, oldScript);
            owner.GetProperty(nameof(PcrAlphaOwner.OfflineMuMuFactory))!.SetValue(null, oldMuMu);
        }
        Require(PcrAlphaOwner.ControllerCreations == 0 && PcrAlphaOwner.AgentCreations == 0
            && PcrAlphaOwner.Grants == 0 && PcrAlphaOwner.SessionCreations == 0, "contracts_never_start_controller_agent_or_grant");
        Console.WriteLine($"PCR alpha owner checks passed: {count}; controller/agent/grant starts=0");
    }
}

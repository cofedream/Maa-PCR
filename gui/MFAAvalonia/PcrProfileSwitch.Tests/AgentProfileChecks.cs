using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls;
using Newtonsoft.Json.Linq;

internal static class AgentProfileChecks
{
    internal static void Run(MaaProcessor processor, TaskQueueViewModel vm, MaaProcessorManager manager)
    {
        const BindingFlags hidden = BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
        var count = 0;
        void Require(bool value, string name) { if (!value) throw new InvalidOperationException(name); count++; }
        void Reject(Action action, string name)
        {
            try { action(); }
            catch (Exception ex) when (ex.GetBaseException() is InvalidOperationException) { count++; return; }
            throw new InvalidOperationException(name);
        }
        var nodes = new JObject { ["PCRAlphaEntry"] = new JObject { ["custom_action_param"] = new JObject { ["selection_mode"] = "pi_nodes_v1" } } };
        foreach (var node in new[] { "Schedule", "Event", "Deep", "Rewards", "VeryHard", "Hard", "Normal", "GuideTrial", "ClanBattleSimulation", "RewardsFinal" })
            nodes["PCRAlphaSelect" + node] = new JObject { ["attach"] = new JObject { ["selected"] = node == "ClanBattleSimulation" } };
        JObject Select() => PcrAlphaOwner.Selection("PCRAlphaEntry", nodes.ToString());
        var selection = Select();
        Require(selection["selected_modules"]!.Values<string>().SequenceEqual(["clan_battle_simulation"])
            && (bool?)selection["rewards_final"] == false, "clan_single_selection");
        foreach (var node in new[] { "GuideTrial", "Rewards", "RewardsFinal" })
        {
            nodes["PCRAlphaSelect" + node]!["attach"]!["selected"] = true;
            Reject(() => Select(), "clan_rejects_mixed_" + node);
            nodes["PCRAlphaSelect" + node]!["attach"]!["selected"] = false;
        }
        var requireProfile = typeof(PcrAlphaOwner).GetMethod("RequireProfileSelection", hidden)!;
        var identity = typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", hidden)!;
        var priorId = identity.GetValue(processor);
        var views = (Dictionary<string, TaskQueueViewModel>)typeof(MaaProcessorManager).GetField("_viewModels", hidden)!.GetValue(manager)!;
        var oldViews = new Dictionary<string, TaskQueueViewModel>(views);
        var tasks = typeof(TaskQueueViewModel).GetField("_taskItemViewModels", hidden)!;
        var resources = typeof(TaskQueueViewModel).GetField("_currentResources", hidden)!;
        var priorTasks = tasks.GetValue(vm); var priorResources = resources.GetValue(vm);
        var confirmed = typeof(TaskQueueViewModel).GetField("<PcrClanGuideConfirmation>k__BackingField", hidden)!;
        var priorConfirmation = confirmed.GetValue(vm);
        var ownerCurrent = typeof(PcrAlphaOwner).GetField("current", hidden)!;
        var ownerPending = typeof(PcrAlphaOwner).GetField("pendingStart", hidden)!;
        object? session = null; object? pending = null;
        var originalCounts = (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations);
        try
        {
            identity.SetValue(processor, "pcr-alpha-v1");
            Reject(() => requireProfile.Invoke(null, [processor, selection]), "daily_profile_rejects_clan");
            identity.SetValue(processor, "pcr-labyrinth-v1");
            Reject(() => requireProfile.Invoke(null, [processor, selection]), "labyrinth_profile_rejects_clan");
            identity.SetValue(processor, PcrClanGuide.ProfileId);
            views[PcrClanGuide.ProfileId] = vm;
            requireProfile.Invoke(null, [processor, selection]); count++;
            Reject(() => requireProfile.Invoke(null, [processor, JObject.Parse("{\"version\":1,\"selected_modules\":[\"rewards\"],\"rewards_final\":false}")]), "agent_rejects_daily_selection");
            Require(vm.IsAgentProfile && !vm.IsDailyProfile && !vm.IsLabyrinthProfile, "agent_has_independent_workspace");
            var requireConfirmation = typeof(TaskQueueViewModel).GetMethod("PcrClanGuideConfirmationForStart", hidden)!;
            confirmed.SetValue(vm, null);
            Reject(() => requireConfirmation.Invoke(vm, null), "agent_without_active_editor_cannot_start_saved_plan");
            confirmed.SetValue(vm, new Func<string?>(() => null));
            Reject(() => requireConfirmation.Invoke(vm, null), "agent_unsaved_editor_cannot_start_saved_plan");
            confirmed.SetValue(vm, new Func<string?>(() => new string('a', 64)));
            Require((string?)requireConfirmation.Invoke(vm, null) == new string('a', 64), "agent_active_editor_confirmation_is_bound");
            var dailyOptions = new List<string> { "rewards" };
            var item = (DragItemViewModel)RuntimeHelpers.GetUninitializedObject(typeof(DragItemViewModel));
            typeof(DragItemViewModel).GetField("_interfaceItem", hidden)!.SetValue(item, new MaaInterface.MaaInterfaceTask {
                Name = "PCRAlpha", Entry = "PCRAlphaEntry", Repeatable = true, RepeatCount = 3,
                PipelineOverride = nodes.Properties().ToDictionary(p => p.Name, p => p.Value.DeepClone()),
                Option = [new MaaInterface.MaaInterfaceSelectOption { Name = "AlphaModules", SelectedCases = dailyOptions }] });
            tasks.SetValue(vm, new ObservableCollection<DragItemViewModel> { item });
            resources.SetValue(vm, new ObservableCollection<MaaInterface.MaaInterfaceResource>());
            var preview = processor.PreviewPcrTask();
            Require(JToken.DeepEquals(PcrAlphaOwner.Selection(preview.Entry!, preview.Param!), selection), "agent_preview_forces_only_clan_without_daily_flags");
            Require(preview.Count == 1, "agent_start_always_has_one_simulation_even_with_legacy_repeat_data");
            Require(dailyOptions.SequenceEqual(["rewards"]), "agent_preview_preserves_saved_daily_options");
            var consent = new PcrStartConsent("", "", [], false);
            var accepted = typeof(PcrStartConsent).GetMethod("Validate", hidden)!.Invoke(null,
                [selection, JObject.Parse("{\"version\":4,\"presets\":[]}"), consent]);
            Require(Equals(accepted, consent), "simulation_does_not_request_daily_count_or_spending_consent");
            var other = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
            var owner = typeof(PcrAlphaOwner);
            object Construct(Type type, params object?[] values) => Activator.CreateInstance(type, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance, null, values, null)!;
            var sessionType = owner.GetNestedType("Session", BindingFlags.NonPublic)!;
            session = Construct(sessionType, processor, selection, Construct(owner.GetNestedType("ValidatedConfig", BindingFlags.NonPublic)!, "{}"), consent);
            ownerCurrent.SetValue(null, session);
            Require((string?)owner.GetProperty("ActiveProfile", hidden)!.GetValue(null) == PcrClanGuide.ProfileId, "owner_retains_agent_source_profile");
            Require(!PcrAlphaOwner.ClaimStart(other), "other_profile_cannot_claim_agent_start");
            PcrAlphaOwner.Revoke(other);
            Require(!(bool)sessionType.GetField("Revoked")!.GetValue(session)!, "other_profile_stop_cannot_revoke_agent");
            PcrAlphaOwner.Revoke(processor);
            Require((bool)sessionType.GetField("Revoked")!.GetValue(session)!
                && ((CancellationTokenSource)sessionType.GetField("Cancel")!.GetValue(session)!).IsCancellationRequested,
                "agent_stop_revokes_original_owner");
            ownerCurrent.SetValue(null, null);
            var pendingType = owner.GetNestedType("PendingStart", BindingFlags.NonPublic)!;
            pending = Construct(pendingType, processor);
            ownerPending.SetValue(null, pending);
            PcrAlphaOwner.Revoke(processor);
            Require((bool)pendingType.GetField("Revoked")!.GetValue(pending)!, "agent_pending_stop_revokes_original_start");
            Require(originalCounts == (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations), "agent_checks_allocate_no_controller_agent_or_grant");
        }
        finally
        {
            ownerCurrent.SetValue(null, null); ownerPending.SetValue(null, null);
            if (session != null) ((CancellationTokenSource)session.GetType().GetField("Cancel")!.GetValue(session)!).Dispose();
            if (pending != null) ((CancellationTokenSource)pending.GetType().GetField("Cancel")!.GetValue(pending)!).Dispose();
            identity.SetValue(processor, priorId); tasks.SetValue(vm, priorTasks); resources.SetValue(vm, priorResources);
            confirmed.SetValue(vm, priorConfirmation);
            views.Clear(); foreach (var pair in oldViews) views[pair.Key] = pair.Value;
        }
        Console.WriteLine($"PCR Agent profile checks passed: {count}; controller/agent/grant starts=0");
    }
}

// Called from the verified native conversation component. Capture the actual
// resolved composer settings; dispatch uses its existing authenticated command.
function sharkRegisterConversation(native) {
  window.sharkVoice?.register({route:native.route, capture() {
    const c = native.read(), thread = native.thread, project = native.project;
    if (!thread || !project || native.blocked || !c?.providerAvailable)
      throw Error("Wait for T3 to connect and choose a project and model.");
    if (c.multipleModelSelections !== null && c.multipleModelSelections !== undefined)
      throw Error("Choose one model for this voice prompt.");
    if (native.newThread && native.envMode === "worktree" && !thread.worktreePath && !native.baseBranch)
      throw Error("Choose a base branch in T3 first.");
    const destination = {environmentId:native.environmentId, environment:native.environment,
      threadId:thread.id, projectId:project.id, project:project.title,
      title:native.newThread ? "New session" : thread.title, new:native.newThread,
      modelSelection:structuredClone(c.selectedModelSelection), runtimeMode:native.runtimeMode,
      interactionMode:c.interactionMode, branch:native.baseBranch ?? null, worktreePath:thread.worktreePath ?? null,
      envMode:native.envMode, startFromOrigin:native.startFromOrigin};
    let created = !native.newThread;
    return {destination, async send(job) {
      if (typeof job.text !== "string" || !job.text.trim() || job.text.length > 20000)
        return {state:"error"};
      const createdAt = new Date().toISOString();
      const input = {threadId:destination.threadId, commandId:job.id,
        message:{messageId:job.messageId, role:"user", text:job.text, attachments:[]},
        modelSelection:destination.modelSelection, runtimeMode:destination.runtimeMode,
        interactionMode:destination.interactionMode, createdAt};
      if (!created) {
        input.titleSeed = job.text.slice(0,60);
        input.bootstrap = {createThread:{projectId:destination.projectId, title:input.titleSeed,
          modelSelection:destination.modelSelection, runtimeMode:destination.runtimeMode,
          interactionMode:destination.interactionMode, branch:destination.branch,
          worktreePath:destination.worktreePath, createdAt:thread.createdAt || createdAt}};
        if (destination.envMode === "worktree" && !destination.worktreePath) {
          input.bootstrap.prepareWorktree = {projectCwd:project.workspaceRoot, baseBranch:destination.branch,
            ...(native.makeWorktreeBranch ? {branch:native.makeWorktreeBranch()} : {}),
            ...(destination.startFromOrigin ? {startFromOrigin:true} : {})};
          input.bootstrap.runSetupScript = true;
        }
      }
      let result;
      try { result = await native.dispatch({environmentId:destination.environmentId, input}); }
      catch { return {state:"uncertain"}; }
      if (result?._tag !== "Success") return {state:"uncertain"};
      const acknowledgement = typeof result.value?.sequence === "number"
        ? {sequence:result.value.sequence}
        : native.protocolVersion === 2 && result.value?.threadId === destination.threadId
          ? {threadId:destination.threadId} : null;
      if (!acknowledgement) return {state:"uncertain"};
      if (!created) {
        created = true;
        // UI promotion must never turn an acknowledged send into a retry.
        try { native.onCreated(); } catch {}
      }
      return {state:"sent", ...acknowledgement};
    }};
  }});
}

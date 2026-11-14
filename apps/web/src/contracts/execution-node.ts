import type { components } from "../generated/api";

export type ExecutionNode = components["schemas"]["RunExecutionNode"];
export const executionActive = (node: ExecutionNode) =>
  node.status === "preparing" || node.status === "running";

/** Semantic checks shared by the BFF and browser, in addition to JSON Schema. */
export class ExecutionSequence {
  private readonly nodes = new Map<string, ExecutionNode>();

  accept(node: ExecutionNode, limits: { executionNodes: number; executionDepth: number }) {
    const previous = this.nodes.get(node.id);
    const dependencies = node.dependsOn ?? [];
    const require = (valid: unknown) => {
      if (!valid) throw new Error("execution_node_order_or_shape");
    };
    require(node.parentId !== node.id && !dependencies.includes(node.id));
    require(new Set(dependencies).size === dependencies.length);
    require(dependencies.every((id) => this.nodes.has(id)));
    if (previous) {
      require(
        node.revision === previous.revision + 1 &&
          node.parentId === previous.parentId &&
          node.startedAtMs === previous.startedAtMs &&
          (node.kind === previous.kind || previous.status === "preparing") &&
          (node.status !== "preparing" || previous.status === "preparing") &&
          executionActive(previous),
      );
    } else {
      require(node.revision === 1 && this.nodes.size < limits.executionNodes);
      const parent = node.parentId ? this.nodes.get(node.parentId) : undefined;
      require(!node.parentId || (parent && executionActive(parent)));
    }
    let depth = 1;
    let parentId = node.parentId;
    while (parentId) {
      const parent = this.nodes.get(parentId);
      require(parent && depth++ < limits.executionDepth);
      parentId = parent!.parentId;
    }
    require(node.endedAtMs == null || node.endedAtMs >= node.startedAtMs);
    require(executionActive(node) === (node.endedAtMs == null));
    const pending = [...dependencies];
    const visited = new Set<string>();
    while (pending.length) {
      const id = pending.pop()!;
      require(id !== node.id);
      if (visited.has(id)) continue;
      visited.add(id);
      pending.push(...(this.nodes.get(id)?.dependsOn ?? []));
    }
    require(node.kind === "group" || node.scheduling == null);
    require(node.kind === "workflow" || node.workflow == null);
    require(node.kind === "skill" || node.skill == null);
    require(node.kind === "subagent" || node.subagent == null);
    require(
      node.origin?.kind !== "mcp" ||
        (node.kind === "tool" && node.origin.serverId && node.origin.toolName),
    );
    require(
      node.display?.policy === "preview" ||
        (node.display?.inputPreview == null && node.display?.outputPreview == null),
    );
    this.nodes.set(node.id, node);
  }
}

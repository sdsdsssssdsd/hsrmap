import type { TreeNode } from "../api/types";

export function indexTree(roots: TreeNode[]) {
  const nodesById = new Map<string, TreeNode>();
  const parentById = new Map<string, string | null>();
  const walk = (nodes: TreeNode[], parent: string | null) => {
    for (const node of nodes) {
      nodesById.set(node.id, node);
      parentById.set(node.id, parent);
      walk(node.children, node.id);
    }
  };
  walk(roots, null);
  return { nodesById, parentById };
}

export function firstRenderable(node: TreeNode): string | null {
  if (node.renderable) return node.id;
  for (const child of node.children) {
    const found = firstRenderable(child);
    if (found) return found;
  }
  return null;
}

export function rootIdOf(id: string, parentById: Map<string, string | null>): string {
  let current = id;
  while (parentById.get(current)) current = parentById.get(current)!;
  return current;
}

export function pathNames(id: string, nodesById: Map<string, TreeNode>, parentById: Map<string, string | null>): string[] {
  const names: string[] = [];
  let current: string | null = id;
  while (current) {
    names.push(nodesById.get(current)?.name || current);
    current = parentById.get(current) ?? null;
  }
  return names.reverse();
}

export function ancestorIds(id: string, parentById: Map<string, string | null>): string[] {
  const ids: string[] = [];
  let current = parentById.get(id);
  while (current) {
    ids.push(current);
    current = parentById.get(current) ?? null;
  }
  return ids;
}

export function containsId(node: TreeNode, id: string): boolean {
  if (node.id === id) return true;
  return node.children.some((child) => containsId(child, id));
}

export function displayChildren(nodes: TreeNode[]): TreeNode[] {
  const special = nodes.filter((node) => node.name === "特殊房间");
  const rest = nodes.filter((node) => node.name !== "特殊房间");
  const wrap = (list: TreeNode[]) => list.map(collapseSameName);
  if (special.length <= 3) return wrap(nodes);
  return [
    ...wrap(rest),
    {
      id: `virtual-special-${special[0].id}`,
      name: `特殊房间 (${special.length})`,
      type: "folder" as const,
      renderable: false,
      children: wrap(special),
    },
  ];
}

function collapseSameName(node: TreeNode): TreeNode {
  if (node.children.length === 1 && node.children[0].name === node.name) {
    const child = collapseSameName(node.children[0]);
    if (!node.renderable && child.renderable) return { ...child, children: displayChildren(child.children) };
  }
  return { ...node, children: displayChildren(node.children) };
}

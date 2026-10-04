import type { TreeNode } from "../api/types";
import { containsId } from "./tree";

interface Props {
  nodes: TreeNode[];
  mapId: string | null;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onActivate: (node: TreeNode) => void;
  depth?: number;
}

export function MapNav({ nodes, mapId, expanded, onToggle, onActivate, depth = 0 }: Props) {
  return (
    <>
      {nodes.map((node) => {
        const open = expanded.has(node.id) || (mapId ? containsId(node, mapId) && !node.renderable : false);
        const rowActive =
          mapId === node.id || (mapId !== null && (node.children.some((child) => child.id === mapId) || (containsId(node, mapId) && !node.renderable && depth === 0)));
        return (
          <div key={node.id}>
            <div
              className={`map-row${rowActive ? " active" : ""}`}
              style={{ paddingLeft: 10 + depth * 14 }}
              onClick={() => onActivate(node)}
            >
              {node.children.length > 0 ? (
                <button
                  className="nav-chevron"
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggle(node.id);
                  }}
                >
                  {open ? "▼" : "▸"}
                </button>
              ) : (
                <span className="nav-chevron" />
              )}
              <span>◆</span>
              <span>{node.name}</span>
            </div>
            {open && node.children.length > 0 && (
              <MapNav
                nodes={node.children}
                mapId={mapId}
                expanded={expanded}
                onToggle={onToggle}
                onActivate={onActivate}
                depth={depth + 1}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

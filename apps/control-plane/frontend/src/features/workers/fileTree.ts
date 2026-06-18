/**
 * File tree model: build a nested tree from flat file paths,
 * with expand/collapse state.
 */

export type FileNode = {
  type: "dir" | "file";
  name: string;
  path: string;
  children?: FileNode[];
};

/**
 * Build a nested directory tree from a flat list of file paths.
 *
 * Example: ["src/a/__init__.py", "src/a/handler.py", "worker.yaml"]
 * → [{type:"dir", name:"src", children:[{type:"dir",name:"a",children:[
 *    {file __init__.py}, {file handler.py}]}]}, {file worker.yaml}]
 */
export function buildTreeFromPaths(paths: string[]): FileNode[] {
  const root: FileNode = { type: "dir", name: "", path: "", children: [] };

  for (const filePath of paths) {
    const segments = filePath.split("/").filter(Boolean);
    let current = root;

    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i];
      const isLast = i === segments.length - 1;
      const segmentPath = segments.slice(0, i + 1).join("/");

      if (isLast) {
        // File node.
        current.children = current.children ?? [];
        const existing = current.children.find(
          (child) => child.path === segmentPath,
        );
        if (!existing) {
          current.children.push({
            type: "file",
            name: segment,
            path: segmentPath,
          });
        }
      } else {
        // Directory node — find or create.
        current.children = current.children ?? [];
        let dirNode = current.children.find(
          (child) => child.type === "dir" && child.path === segmentPath,
        );
        if (!dirNode) {
          dirNode = {
            type: "dir",
            name: segment,
            path: segmentPath,
            children: [],
          };
          current.children.push(dirNode);
        }
        current = dirNode as FileNode;
      }
    }
  }

  // Sort: directories first, then files, alphabetically.
  function sortTree(nodes: FileNode[]): FileNode[] {
    return nodes
      .sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        return a.name.localeCompare(b.name);
      })
      .map((node) =>
        node.type === "dir" && node.children
          ? { ...node, children: sortTree(node.children) }
          : node,
      );
  }

  return sortTree(root.children ?? []);
}

/**
 * Flatten a tree into a list of file paths (depth-first).
 */
export function flattenFilePaths(nodes: FileNode[]): string[] {
  const result: string[] = [];
  function walk(node: FileNode) {
    if (node.type === "file") {
      result.push(node.path);
    } else {
      node.children?.forEach(walk);
    }
  }
  nodes.forEach(walk);
  return result;
}

/**
 * Check if a file path is under a directory path.
 */
export function isUnderDirectory(filePath: string, dirPath: string): boolean {
  return filePath === dirPath || filePath.startsWith(dirPath + "/");
}

/**
 * Collect all directory paths in a tree (for expand/collapse state init).
 */
export function collectDirectoryPaths(nodes: FileNode[]): string[] {
  const result: string[] = [];
  function walk(node: FileNode) {
    if (node.type === "dir") {
      result.push(node.path);
      node.children?.forEach(walk);
    }
  }
  nodes.forEach(walk);
  return result;
}

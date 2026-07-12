import { create } from "zustand";

export type NavSection =
  | "command-center"
  | "services"
  | "workers"
  | "agents"
  | "connectors"
  | "notifications"
  | "settings";

type WorkbenchState = {
  section: NavSection;
  inspectorOpen: boolean;
  setSection: (section: NavSection) => void;
  setInspectorOpen: (open: boolean) => void;
};

export const useWorkbenchStore = create<WorkbenchState>((set) => ({
  section: "command-center",
  inspectorOpen: true,
  setSection: (section) => set({ section }),
  setInspectorOpen: (inspectorOpen) => set({ inspectorOpen }),
}));

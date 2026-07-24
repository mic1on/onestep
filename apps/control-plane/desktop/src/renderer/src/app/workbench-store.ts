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
  selectedServiceName: string | null;
  inspectorOpen: boolean;
  setSection: (section: NavSection) => void;
  setSelectedServiceName: (name: string | null) => void;
  setInspectorOpen: (open: boolean) => void;
};

export const useWorkbenchStore = create<WorkbenchState>((set) => ({
  section: "command-center",
  selectedServiceName: null,
  inspectorOpen: true,
  setSection: (section) => set({ section }),
  setSelectedServiceName: (selectedServiceName) => set({ selectedServiceName }),
  setInspectorOpen: (inspectorOpen) => set({ inspectorOpen }),
}));

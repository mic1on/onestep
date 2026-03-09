import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/layout/AppShell";
import { InstanceDetailPage } from "../pages/instance-detail/InstanceDetailPage";
import { NotFoundPage } from "../pages/not-found/NotFoundPage";
import { ServiceDetailPage } from "../pages/service-detail/ServiceDetailPage";
import { ServicesListPage } from "../pages/services-list/ServicesListPage";
import { TaskDetailPage } from "../pages/task-detail/TaskDetailPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <Navigate to="/services?environment=prod" replace />,
      },
      {
        path: "services",
        element: <ServicesListPage />,
      },
      {
        path: "services/:serviceName",
        element: <ServiceDetailPage />,
      },
      {
        path: "services/:serviceName/tasks/:taskName",
        element: <TaskDetailPage />,
      },
      {
        path: "services/:serviceName/instances/:instanceId",
        element: <InstanceDetailPage />,
      },
      {
        path: "*",
        element: <NotFoundPage />,
      },
    ],
  },
]);

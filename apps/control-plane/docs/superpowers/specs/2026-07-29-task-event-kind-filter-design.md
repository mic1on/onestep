# Task Event Kind Filter Design

## Goal

Add a multi-select event-kind filter to the task event history panel. Filtering must apply to the complete server-side result set so that the displayed total and pagination remain accurate.

## Scope

The filter covers both sources already merged into task event history:

- runtime lifecycle events: `started`, `succeeded`, `failed`, `retried`, `dead_lettered`, and `cancelled`
- control commands: `pause_task`, `resume_task`, `restart_task`, `discard_dead_letters`, `replay_dead_letters`, and `run_task_once`

No reporter payload, WebSocket protocol, database schema, or stored event semantics change.

## Interaction

Place a type-filter button beside the existing lookback control in the task event panel header. The button opens a checkbox menu grouped into runtime events and control commands.

No selected kinds means all event kinds. When kinds are selected, the button shows the selected count. The menu provides a clear action that returns to all event kinds. Each selection change applies immediately and resets pagination to the first page.

The control must have English and Simplified Chinese labels, keyboard-accessible buttons and checkboxes, and a layout that remains usable when the header wraps on narrow screens.

## Data Flow

`App` owns the selected kind list alongside the task event lookback and offset. It passes the selection to `loadTaskEventLogs` and into `TaskEventDiagnostics` for rendering and changes.

`loadTaskEventLogs` sends selected kinds as repeated `kind` query parameters. An empty list omits the parameter and preserves current behavior.

The task event history endpoint accepts zero or more `kind` values. Its query helper applies the selected values independently to runtime events and control commands before calculating the combined total, ordering the union, and applying offset and limit. A selected runtime kind cannot match a command row, and a selected command kind cannot match a runtime row.

## Error Handling And Compatibility

The API validates kind values against the event kinds exposed by task history and returns the normal request-validation response for unsupported values. Existing callers that omit `kind` continue to receive all events.

Changing tasks resets the event offset but retains the selected filter, matching the existing retained lookback behavior. A filtered empty result uses the existing empty-state presentation.

## Testing

Backend tests will cover runtime-only, command-only, and mixed multi-kind filters, including accurate totals and pagination after filtering. Frontend API tests will verify repeated query parameters and response mapping. Component and app-level tests will cover selecting multiple kinds, clearing the selection, displaying the selected count, and resetting the offset when the filter changes.

Run the focused backend and frontend test suites, then the frontend production build. Because frontend and backend code are baked into the control-plane image, rebuild and restart the `plane` service and verify that the container is healthy.

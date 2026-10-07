export const STATUS_LABELS = {
  not_started: "Not started",
  in_progress: "In progress",
  awaiting_external: "Awaiting external",
  completed: "Completed",
  not_applicable: "Not applicable",
};

export const ROLE_LABELS = {
  md: "Managing Director",
  manager: "Manager",
  staff: "Staff",
  sales: "Sales",
  viewer: "Viewer",
};

export const STALENESS_LABELS = { green: "Fresh", amber: "Update due", red: "Stale" };

export const FIELD_LABELS = {
  status: "Status",
  stage: "Stage",
  owner: "Owner",
  department: "Department",
  external_party: "External party",
  reason: "Reason",
  next_action: "Next action",
  next_review_date: "Next review",
};

export const statusLabel = (s) => STATUS_LABELS[s] || s;

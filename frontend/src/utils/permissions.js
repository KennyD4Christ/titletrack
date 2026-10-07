// The matrix is GENERATED from the backend (python manage.py export_permissions); a backend test fails
// if it goes stale. Hiding a menu item here is a convenience only: the API is what enforces permissions.
import matrix from "./permissions.matrix.json";

export const can = (user, action) =>
  Boolean(user) && Array.isArray(matrix.actions[action]) && matrix.actions[action].includes(user.role);

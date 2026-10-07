import { describe, expect, it } from "vitest";
import { can } from "./permissions.js";
import matrix from "./permissions.matrix.json";

describe("permissions (generated from the backend matrix)", () => {
  it("answers per role", () => {
    expect(can({ role: "md" }, "view_dashboard")).toBe(true);
    expect(can({ role: "staff" }, "view_dashboard")).toBe(false);
    expect(can({ role: "staff" }, "view_my_queue")).toBe(true);
    expect(can({ role: "sales" }, "view_my_queue")).toBe(false);
    expect(can({ role: "viewer" }, "import_data")).toBe(false);
    expect(can({ role: "manager" }, "import_data")).toBe(true);
  });
  it("denies unknown actions and signed-out users", () => {
    expect(can({ role: "md" }, "made_up")).toBe(false);
    expect(can(null, "view_home")).toBe(false);
  });
  it("only references known roles", () => {
    for (const roles of Object.values(matrix.actions)) {
      for (const role of roles) expect(matrix.roles).toContain(role);
    }
  });
});

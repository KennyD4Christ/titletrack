import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, fixtures, json, mockApi, renderApp } from "./test/helpers.jsx";

const ROLES = [
  { value: "md", label: "MD / Director" }, { value: "manager", label: "Documentation / Operations manager" },
  { value: "staff", label: "Staff" }, { value: "sales", label: "Sales" }, { value: "viewer", label: "Viewer" },
];
const DEPARTMENTS = { count: 2, next: null, previous: null, results: [{ id: 4, name: "Documentation" }, { id: 5, name: "Survey" }] };
const page = (results) => ({ count: results.length, next: null, previous: null, results });

const person = (over) => ({
  id: 0, username: "", email: "", first_name: "", last_name: "", full_name: "", role: "viewer", department: null,
  department_name: null, is_active: true, last_login: null, open_obligations: 0, is_protected: false,
  can_manage: true, is_self: false, ...over,
});
const ME = person({ id: 8, username: "md_demohomes", email: "folake@demo.test", first_name: "Folake", last_name: "Adeyemi", full_name: "Folake Adeyemi", role: "md", is_self: true });
const OTHER_MD = person({ id: 20, email: "chidi@demo.test", first_name: "Chidi", last_name: "Obi", full_name: "Chidi Obi", role: "md" });
const MANAGER = person({ id: 9, email: "emeka@demo.test", first_name: "Emeka", last_name: "Okafor", full_name: "Emeka Okafor", role: "manager" });
const AISHA = person({ id: 12, email: "aisha@demo.test", first_name: "Aisha", last_name: "Bello", full_name: "Aisha Bello", role: "staff", department: 4, department_name: "Documentation", open_obligations: 3 });
const KENNY = person({ id: 30, email: "", username: "KennyD", first_name: "Kenny", last_name: "D", full_name: "Kenny D", role: "md", is_protected: true, can_manage: false });
const OLD = person({ id: 31, email: "old@demo.test", first_name: "Old", last_name: "Timer", full_name: "Old Timer", role: "viewer", is_active: false });
const MD_ROWS = [ME, OTHER_MD, MANAGER, AISHA, KENNY, OLD];

const routes = (extra = {}, role = "md", rows = MD_ROWS, roles = ROLES) =>
  baseRoutes(role, {
    "GET /api/staff/": page(rows),
    "GET /api/staff/options/": { roles },
    "GET /api/departments/": DEPARTMENTS,
    ...extra,
  });

const body = (c) => JSON.parse(c.init.body);
const posts = (api, end) => api.calls.filter((c) => c.method === "POST" && c.url.endsWith(end));
const listUrls = (api) => api.urls().filter((u) => u.startsWith("/api/staff/?"));
const row = async (name) => within(await screen.findByRole("table")).getByText(name).closest("tr");
const open = async (path = "/staff", extra, role, rows, roles) => {
  const api = mockApi(routes(extra, role, rows, roles));
  renderApp(path);
  await screen.findByRole("heading", { name: "Staff" });
  await screen.findByRole("table");
  return api;
};
const LINK = "https://app.example.test/set-password#token=1.setup.1.abc:1xYz:SIG";
const clipboard = () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
};

describe("staff list", () => {
  it("shows role, department, status and open obligations, and keeps protected accounts read-only", async () => {
    await open();
    const aisha = await row("Aisha Bello");
    expect(within(aisha).getByText("Staff")).toBeInTheDocument();
    expect(within(aisha).getByText("Documentation")).toBeInTheDocument();
    expect(within(aisha).getByText("Active")).toBeInTheDocument();
    expect(within(aisha).getByText("3")).toBeInTheDocument();
    for (const label of ["Edit", "Setup link", "Reset link", "Deactivate", "History"]) {
      expect(within(aisha).getByRole("button", { name: `${label} Aisha Bello` })).toBeInTheDocument();
    }
    const kenny = await row("Kenny D");
    expect(within(kenny).getByText("Admin account")).toBeInTheDocument();
    expect(within(kenny).getByText("Protected account")).toBeInTheDocument();
    for (const label of ["Edit", "Setup link", "Reset link", "Deactivate", "Reactivate"]) {
      expect(within(kenny).queryByRole("button", { name: `${label} Kenny D` })).not.toBeInTheDocument();
    }
    expect(within(kenny).getByRole("button", { name: "History Kenny D" })).toBeInTheDocument();    // an MD may read it
  });

  it("does not offer deactivation or links on your own row", async () => {
    await open();
    const me = await row("Folake Adeyemi");
    expect(within(me).getByText("You")).toBeInTheDocument();
    expect(within(me).getByRole("button", { name: "Edit Folake Adeyemi" })).toBeInTheDocument();
    for (const label of ["Deactivate", "Setup link", "Reset link"]) {
      expect(within(me).queryByRole("button", { name: `${label} Folake Adeyemi` })).not.toBeInTheDocument();
    }
  });

  it("offers Reactivate (only) for a deactivated account", async () => {
    await open();
    const old = await row("Old Timer");
    expect(within(old).getByText("Deactivated")).toBeInTheDocument();
    expect(old).toHaveClass("row-cancelled");
    expect(within(old).getByRole("button", { name: "Reactivate Old Timer" })).toBeInTheDocument();
    for (const label of ["Deactivate", "Setup link", "Reset link"]) {
      expect(within(old).queryByRole("button", { name: `${label} Old Timer` })).not.toBeInTheDocument();
    }
  });

  it("shows a Manager the MD and Manager accounts read-only", async () => {
    const rows = [ME, OTHER_MD, MANAGER, AISHA].map((r) => (["md", "manager"].includes(r.role) ? { ...r, can_manage: false } : r))
      .map((r) => (r.id === 9 ? { ...r, is_self: true } : { ...r, is_self: false }));
    mockApi(routes({ "GET /api/auth/me/": fixtures.me.manager }, "manager", rows, ROLES.slice(2)));
    renderApp("/staff");
    await screen.findByRole("table");
    for (const name of ["Folake Adeyemi", "Chidi Obi"]) {
      const r = await row(name);
      expect(within(r).getByText("Read only")).toBeInTheDocument();
      expect(within(r).queryByRole("button")).not.toBeInTheDocument();             // not even History
    }
    expect(within(await row("Aisha Bello")).getByRole("button", { name: "Edit Aisha Bello" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add account" })).toBeInTheDocument();
  });

  it.each(["staff", "sales", "viewer"])("%s has no Staff menu item and cannot open the page", async (role) => {
    const api = mockApi(routes({}, role));
    renderApp("/staff");
    expect(await screen.findByRole("heading", { name: "You do not have access to this page" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Staff" })).not.toBeInTheDocument();
    expect(api.urls().some((u) => u.startsWith("/api/staff/"))).toBe(false);
  });

  it.each(["md", "manager"])("%s sees the Staff menu item", async (role) => {
    mockApi(routes({}, role));
    renderApp("/staff");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Staff" })).toBeInTheDocument();
  });

  it("turns the address into API filters and ignores unknown ones", async () => {
    const api = mockApi(routes());
    renderApp("/staff?q=aisha&role=staff&is_active=1&junk=1");
    await screen.findByRole("heading", { name: "Staff" });
    await waitFor(() => expect(listUrls(api).length).toBeGreaterThan(0));
    expect(Object.fromEntries(new URL(listUrls(api).at(-1), "http://x").searchParams)).toEqual({
      q: "aisha", role: "staff", is_active: "1", page: "1", page_size: "25",
    });
  });

  it("searches, filters and clears", async () => {
    const api = await open();
    await userEvent.type(screen.getByRole("searchbox"), "Bello{Enter}");
    await waitFor(() => expect(listUrls(api).at(-1)).toContain("q=Bello"));
    await userEvent.selectOptions(screen.getByLabelText("Role"), "Staff");
    await waitFor(() => expect(listUrls(api).at(-1)).toContain("role=staff"));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "Deactivated");
    await waitFor(() => expect(listUrls(api).at(-1)).toContain("is_active=0"));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(listUrls(api).at(-1)).not.toContain("role="));
    expect(listUrls(api).at(-1)).not.toContain("q=");
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });

  it("pages forwards", async () => {
    const api = await open("/staff", { "GET /api/staff/": { count: 80, next: null, previous: null, results: MD_ROWS } });
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(listUrls(api).at(-1)).toContain("page=2"));
  });

  it("says so when nothing matches, and offers a retry when loading fails", async () => {
    mockApi(routes({ "GET /api/staff/": page([]) }));
    renderApp("/staff");
    expect(await screen.findByText("No accounts match these filters.")).toBeInTheDocument();
  });

  it("shows a loading state, then an error with Try again", async () => {
    let fail = true;
    mockApi(routes({ "GET /api/staff/": () => (fail ? json({ detail: "Boom." }, 500) : page(MD_ROWS)) }));
    renderApp("/staff");
    expect(await screen.findByRole("alert")).toHaveTextContent("Boom.");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });
});

describe("adding an account", () => {
  const openAdd = async (extra, role, rows, roles) => {
    const api = await open("/staff", extra, role, rows, roles);
    await userEvent.click(screen.getByRole("button", { name: "Add account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an account" });
    await within(dialog).findByRole("option", { name: "Documentation" });
    return { api, dialog };
  };
  const fill = async (dialog, { first = "Nia", last = "Eze", email = "nia@demo.test", role = "Viewer" } = {}) => {
    await userEvent.type(within(dialog).getByLabelText("First name"), first);
    await userEvent.type(within(dialog).getByLabelText("Last name"), last);
    await userEvent.type(within(dialog).getByLabelText("Email"), email);
    await userEvent.selectOptions(within(dialog).getByLabelText("Role"), role);
  };
  const NEW = person({ id: 40, email: "nia@demo.test", first_name: "Nia", last_name: "Eze", full_name: "Nia Eze", role: "staff", department: 4, department_name: "Documentation" });
  const issued = (status, extra = {}) => ({ purpose: "setup", status, expires_at: "2026-10-19T09:30:00Z", ...extra });

  it("creates a Staff account (a department is required) and shows the one-time setup link", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const writeText = clipboard();
    const { api, dialog } = await openAdd({
      "POST /api/staff/": () => json(NEW, 201),
      "POST /api/staff/40/setup-link/": () => issued("revealed", { link: LINK }),
    });
    const submit = within(dialog).getByRole("button", { name: "Add account" });
    expect(submit).toBeDisabled();
    await fill(dialog, { role: "Staff" });
    expect(submit).toBeDisabled();                                            // Staff needs a department
    await userEvent.selectOptions(within(dialog).getByLabelText("Department"), "Documentation");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);

    const linkDialog = await screen.findByRole("dialog", { name: "Setup link for Nia Eze" });
    expect(body(posts(api, "/api/staff/")[0])).toEqual({
      email: "nia@demo.test", first_name: "Nia", last_name: "Eze", role: "staff", department: 4,
    });
    expect(body(posts(api, "/setup-link/")[0])).toEqual({ reveal_if_email_fails: false });
    expect(within(linkDialog).getByLabelText("One-time link")).toHaveValue(LINK);
    expect(within(linkDialog).getByText(/works once and expires in 3 days/)).toBeInTheDocument();
    await userEvent.click(within(linkDialog).getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(await within(linkDialog).findByText("Copied.")).toBeInTheDocument();

    await userEvent.click(within(linkDialog).getByRole("button", { name: "Done" }));
    expect(screen.queryByDisplayValue(LINK)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("token=");                   // the secret is gone from the page
    expect(screen.getByText("Nia Eze was added.")).toBeInTheDocument();
    expect(listUrls(api).length).toBeGreaterThan(1);                             // the list was reloaded
  });

  it("lets other roles skip the department, and sends no link when the box is unticked", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openAdd({ "POST /api/staff/": () => json({ ...NEW, role: "viewer", department: null }, 201) });
    await fill(dialog, { role: "Viewer" });
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Send the setup link now" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await screen.findByText(/Nia Eze was added\. Use "Setup link"/)).toBeInTheDocument();
    expect(body(posts(api, "/api/staff/")[0])).toMatchObject({ role: "viewer", department: null });
    expect(posts(api, "/setup-link/")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("says when the link was emailed (and shows no link)", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { dialog } = await openAdd({
      "POST /api/staff/": () => json(NEW, 201),
      "POST /api/staff/40/setup-link/": () => issued("emailed"),
    });
    await fill(dialog, { role: "Viewer" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await screen.findByText("Nia Eze was added and the setup link was emailed to nia@demo.test.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "One-time link" })).not.toBeInTheDocument();
  });

  it("keeps the account when only the link step fails, and says what to do", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { dialog } = await openAdd({
      "POST /api/staff/": () => json(NEW, 201),
      "POST /api/staff/40/setup-link/": () => json({ detail: "Mail server is down." }, 500),
    });
    await fill(dialog, { role: "Viewer" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await screen.findByText(/Nia Eze was added, but the setup link could not be issued: Mail server is down\./)).toBeInTheDocument();
  });

  it("explains an email that could not be sent", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { dialog } = await openAdd({
      "POST /api/staff/": () => json(NEW, 201),
      "POST /api/staff/40/setup-link/": () => issued("email_failed", { detail: "The email could not be sent." }),
    });
    await fill(dialog, { role: "Viewer" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await screen.findByText(/the setup email could not be sent/)).toBeInTheDocument();
  });

  it("shows the server's field errors and keeps the dialog open", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openAdd({
      "POST /api/staff/": () => json({ email: ["This email address is not available."] }, 400),
    });
    await fill(dialog, { role: "Viewer" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await within(dialog).findByText("This email address is not available.")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Add an account" })).toBeInTheDocument();
    expect(posts(api, "/setup-link/")).toHaveLength(0);
  });

  it("shows a refusal such as 'your role cannot do this'", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { dialog } = await openAdd({ "POST /api/staff/": () => json({ detail: "Your role cannot create MD / Director accounts." }, 403) });
    await fill(dialog, { role: "Viewer" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Your role cannot create MD / Director accounts.");
  });

  it("offers an MD every role", async () => {
    const { dialog } = await openAdd();
    expect(within(within(dialog).getByLabelText("Role")).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Choose a role", "MD / Director", "Documentation / Operations manager", "Staff", "Sales", "Viewer"]);
  });

  it("offers a Manager only Staff, Sales and Viewer", async () => {
    const { dialog } = await openAdd({ "GET /api/auth/me/": fixtures.me.manager }, "manager", MD_ROWS, ROLES.slice(2));
    expect(within(within(dialog).getByLabelText("Role")).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Choose a role", "Staff", "Sales", "Viewer"]);
  });

  it("closes without saving", async () => {
    const { api, dialog } = await openAdd();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });
});

describe("editing an account", () => {
  const openEdit = async (name, extra) => {
    const api = await open("/staff", extra);
    await userEvent.click(within(await row(name)).getByRole("button", { name: `Edit ${name}` }));
    return { api, dialog: await screen.findByRole("dialog", { name: `Edit ${name}` }) };
  };

  it("sends only what changed and never offers the email as an input", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openEdit("Aisha Bello", { "PATCH /api/staff/12/": () => json({ ...AISHA, first_name: "Aisha B.", full_name: "Aisha B. Bello" }) });
    expect(within(dialog).queryByRole("textbox", { name: "Email" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("aisha@demo.test")).toBeInTheDocument();
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText("First name"));
    await userEvent.type(within(dialog).getByLabelText("First name"), "Aisha B.");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(await screen.findByText("Aisha B. Bello was updated.")).toBeInTheDocument();
    const patch = api.calls.find((c) => c.method === "PATCH");
    expect(body(patch)).toEqual({ first_name: "Aisha B." });
  });

  it("can change role and department together", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openEdit("Aisha Bello", { "PATCH /api/staff/12/": () => json(AISHA) });
    await userEvent.selectOptions(within(dialog).getByLabelText("Role"), "Sales");
    await userEvent.selectOptions(within(dialog).getByLabelText("Department"), "Survey");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(body(api.calls.find((c) => c.method === "PATCH"))).toEqual({ role: "sales", department: 5 });
  });

  it("will not save a Staff account without a department", async () => {
    const { dialog } = await openEdit("Aisha Bello");
    await userEvent.selectOptions(within(dialog).getByLabelText("Department"), "No department");
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("locks your own role", async () => {
    const { dialog } = await openEdit("Folake Adeyemi");
    expect(within(dialog).getByLabelText("Role")).toBeDisabled();
    expect(within(dialog).getByText(/You cannot change your own role/)).toBeInTheDocument();
  });

  it("shows a server refusal in the dialog", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { dialog } = await openEdit("Aisha Bello", { "PATCH /api/staff/12/": () => json({ detail: "A company must keep at least one active MD." }, 409) });
    await userEvent.clear(within(dialog).getByLabelText("Last name"));
    await userEvent.type(within(dialog).getByLabelText("Last name"), "Z");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("A company must keep at least one active MD.");
  });
});

describe("deactivating and reactivating", () => {
  const openDeactivate = async (extra, name = "Aisha Bello") => {
    const api = await open("/staff", extra);
    await userEvent.click(within(await row(name)).getByRole("button", { name: `Deactivate ${name}` }));
    return { api, dialog: await screen.findByRole("dialog", { name: `Deactivate ${name}` }) };
  };

  it("shows the open obligations first, links to them, and deactivates without reassigning", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openDeactivate({
      "GET /api/staff/12/open-obligations/": { owner: 12, count: 3 },
      "POST /api/staff/12/deactivate/": () => json({ ...AISHA, is_active: false, open_obligations_remaining: 3 }),
    });
    const confirm = within(dialog).getByRole("button", { name: "Deactivate account" });
    const warning = await within(dialog).findByText(/3 open obligations are assigned to Aisha Bello\./);
    expect(warning.closest(".banner")).toHaveTextContent("they stay assigned to this person until someone reassigns them");
    expect(within(dialog).getByRole("link", { name: "See their open obligations" })).toHaveAttribute("href", "/obligations?owner=12&open=1");
    await userEvent.type(within(dialog).getByLabelText("Reason (optional)"), "Resigned");
    await userEvent.click(confirm);
    expect(await screen.findByText(/Aisha Bello was deactivated\. 3 open obligations still belong to them until reassigned\./)).toBeInTheDocument();
    expect(body(posts(api, "/deactivate/")[0])).toEqual({ reason: "Resigned" });
    expect(screen.getByRole("link", { name: "Reassign them from the obligations list" })).toHaveAttribute("href", "/obligations?owner=12&open=1");
    expect(api.calls.some((c) => c.url.startsWith("/api/obligations/") && c.method !== "GET")).toBe(false);   // nothing reassigned
  });

  it("waits for the count before the button works", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const { dialog } = await openDeactivate({
      "GET /api/staff/12/open-obligations/": async () => { await gate; return { owner: 12, count: 0 }; },
    });
    expect(within(dialog).getByRole("button", { name: "Deactivate account" })).toBeDisabled();
    expect(within(dialog).getByText("Checking their open obligations…")).toBeInTheDocument();
    release();
    expect(await within(dialog).findByText("They have no open obligations.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Deactivate account" })).toBeEnabled();
    expect(within(dialog).queryByRole("link", { name: "See their open obligations" })).not.toBeInTheDocument();
  });

  it("deactivates a person with nothing open, with no reason", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openDeactivate({
      "GET /api/staff/12/open-obligations/": { owner: 12, count: 0 },
      "POST /api/staff/12/deactivate/": () => json({ ...AISHA, is_active: false, open_obligations_remaining: 0 }),
    });
    await userEvent.click(await within(dialog).findByRole("button", { name: "Deactivate account" }).then((b) => (expect(b).toBeEnabled(), b)));
    expect(await screen.findByText("Aisha Bello was deactivated.")).toBeInTheDocument();
    expect(body(posts(api, "/deactivate/")[0])).toEqual({});
  });

  it("will not deactivate when the count cannot be read, until it can", async () => {
    let fail = true;
    const { dialog } = await openDeactivate({
      "GET /api/staff/12/open-obligations/": () => (fail ? json({ detail: "Down." }, 500) : { owner: 12, count: 1 }),
    });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not check their open obligations: Down.");
    expect(within(dialog).getByRole("button", { name: "Deactivate account" })).toBeDisabled();
    fail = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText(/1 open obligation is assigned to Aisha Bello\./)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Deactivate account" })).toBeEnabled();
  });

  it("shows a refusal and changes nothing; Not now closes", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openDeactivate({
      "GET /api/staff/12/open-obligations/": { owner: 12, count: 0 },
      "POST /api/staff/12/deactivate/": () => json({ detail: "This account is already deactivated." }, 409),
    });
    await userEvent.click(await within(dialog).findByRole("button", { name: "Deactivate account" }));
    await waitFor(() => expect(within(dialog).getAllByRole("alert").some((a) => /already deactivated/.test(a.textContent))).toBe(true));
    await userEvent.click(within(dialog).getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api, "/deactivate/")).toHaveLength(1);
  });

  it("reactivates after a confirmation", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = await open("/staff", { "POST /api/staff/31/reactivate/": () => json({ ...OLD, is_active: true }) });
    await userEvent.click(within(await row("Old Timer")).getByRole("button", { name: "Reactivate Old Timer" }));
    const dialog = await screen.findByRole("dialog", { name: "Reactivate Old Timer" });
    expect(posts(api, "/reactivate/")).toHaveLength(0);                                 // nothing happens until confirmed
    await userEvent.click(within(dialog).getByRole("button", { name: "Reactivate account" }));
    expect(await screen.findByText("Old Timer was reactivated.")).toBeInTheDocument();
    expect(posts(api, "/reactivate/")).toHaveLength(1);
  });
});

describe("setup and reset links", () => {
  const openLink = async (kind, extra) => {
    const api = await open("/staff", extra);
    await userEvent.click(within(await row("Aisha Bello")).getByRole("button", { name: `${kind} Aisha Bello` }));
    const title = kind === "Setup link" ? "Setup link for Aisha Bello" : "Password reset link for Aisha Bello";
    return { api, dialog: await screen.findByRole("dialog", { name: title }) };
  };
  const outcome = (status, extra = {}) => ({ purpose: "setup", status, expires_at: "2026-10-19T09:30:00Z", ...extra });

  it("asks first, then shows a one-time setup link with a copy button, and forgets it when closed", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const writeText = clipboard();
    const { api, dialog } = await openLink("Setup link", { "POST /api/staff/12/setup-link/": () => outcome("revealed", { link: LINK }) });
    expect(within(dialog).getByText(/expires in 3 days/)).toBeInTheDocument();
    expect(posts(api, "/setup-link/")).toHaveLength(0);                                // nothing issued until asked
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue setup link" }));
    expect(await within(dialog).findByLabelText("One-time link")).toHaveValue(LINK);
    expect(within(dialog).getByText(/shown only once and is not stored anywhere/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(LINK);
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(document.body.textContent).not.toContain("token=");
    await userEvent.click(within(await row("Aisha Bello")).getByRole("button", { name: "Setup link Aisha Bello" }));
    const again = await screen.findByRole("dialog", { name: "Setup link for Aisha Bello" });
    expect(within(again).queryByLabelText("One-time link")).not.toBeInTheDocument();       // it does not come back
    expect(within(again).getByRole("button", { name: "Issue setup link" })).toBeInTheDocument();
  });

  it("uses the reset endpoint and the one-day lifetime for reset links", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openLink("Reset link", {
      "POST /api/staff/12/reset-link/": () => outcome("revealed", { purpose: "reset", link: LINK.replace("set-password", "reset-password") }),
    });
    expect(within(dialog).getByText(/expires in 1 day\./)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue password reset link" }));
    expect((await within(dialog).findByLabelText("One-time link")).value).toContain("/reset-password#token=");
    expect(body(posts(api, "/reset-link/")[0])).toEqual({ reveal_if_email_fails: false });
  });

  it("shows nothing secret when the link was emailed", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { dialog } = await openLink("Setup link", { "POST /api/staff/12/setup-link/": () => outcome("emailed") });
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue setup link" }));
    expect(await within(dialog).findByText(/was emailed to aisha@demo\.test/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("after a failed email, shows the link only when explicitly asked", async () => {
    document.cookie = "csrftoken=abc; path=/";
    let calls = 0;
    const { api, dialog } = await openLink("Setup link", {
      "POST /api/staff/12/setup-link/": (url, init) => {
        calls += 1;
        return JSON.parse(init.body).reveal_if_email_fails ? outcome("revealed", { link: LINK }) : outcome("email_failed", { detail: "The email could not be sent." });
      },
    });
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue setup link" }));
    expect(await within(dialog).findByText(/The email could not be sent, so nobody has this link yet/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("One-time link")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Show the link here instead" }));
    expect(await within(dialog).findByLabelText("One-time link")).toHaveValue(LINK);
    expect(calls).toBe(2);
    expect(posts(api, "/setup-link/").map(body)).toEqual([{ reveal_if_email_fails: false }, { reveal_if_email_fails: true }]);
  });

  it("explains a refusal or the rate limit in the dialog", async () => {
    document.cookie = "csrftoken=abc; path=/";
    let status = 403;
    const { dialog } = await openLink("Setup link", {
      "POST /api/staff/12/setup-link/": () => (status === 403
        ? json({ detail: "Your role cannot manage MD / Director accounts." }, 403)
        : json({ detail: "Request was throttled." }, 429)),
    });
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue setup link" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Your role cannot manage MD / Director accounts.");
    status = 429;
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue setup link" }));
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent("Too many attempts. Please wait a while and try again."));
  });
});

describe("account history", () => {
  const EVENTS = {
    count: 3, next: null, previous: null,
    results: [
      { id: 1, created_at: "2026-10-01T08:00:00Z", action: "created", action_label: "Account created", actor: 8, actor_name: "Folake Adeyemi", target_email: "aisha@demo.test", changes: { role: [null, "staff"], department: [null, "Documentation"] }, note: "" },
      { id: 2, created_at: "2026-10-05T08:00:00Z", action: "role_changed", action_label: "Role changed", actor: 8, actor_name: "Folake Adeyemi", target_email: "aisha@demo.test", changes: { role: ["staff", "sales"] }, note: "" },
      { id: 3, created_at: "2026-10-09T08:00:00Z", action: "deactivated", action_label: "Account deactivated", actor: null, actor_name: null, target_email: "aisha@demo.test", changes: { is_active: [true, false] }, note: "Resigned\n3 open obligation(s) remain assigned to this person." },
    ],
  };

  it("lists who did what, with before and after values", async () => {
    const api = await open("/staff", { "GET /api/staff/12/history/": EVENTS });
    await userEvent.click(within(await row("Aisha Bello")).getByRole("button", { name: "History Aisha Bello" }));
    const dialog = await screen.findByRole("dialog", { name: "History of Aisha Bello" });
    expect(await within(dialog).findByText("Account created")).toBeInTheDocument();
    expect(within(dialog).getByText("Role: Staff")).toBeInTheDocument();                       // first value: "set to"
    expect(within(dialog).getByText("Department: Documentation")).toBeInTheDocument();
    expect(within(dialog).getByText("Role: Staff → Sales")).toBeInTheDocument();
    expect(within(dialog).getByText("Active: Yes → No")).toBeInTheDocument();
    expect(within(dialog).getByText("System")).toBeInTheDocument();                           // no actor
    expect(within(dialog).getAllByText("Folake Adeyemi")).toHaveLength(2);
    expect(within(dialog).getByText(/3 open obligation\(s\) remain assigned/)).toBeInTheDocument();
    expect(api.urls().some((u) => u.startsWith("/api/staff/12/history/?") && u.includes("page=1") && u.includes("page_size=20"))).toBe(true);
    expect(dialog.textContent).not.toMatch(/token|password|http/i);
  });

  it("pages, shows an empty state and an error", async () => {
    const api = await open("/staff", { "GET /api/staff/12/history/": { ...EVENTS, count: 45 } });
    await userEvent.click(within(await row("Aisha Bello")).getByRole("button", { name: "History Aisha Bello" }));
    const dialog = await screen.findByRole("dialog", { name: "History of Aisha Bello" });
    await userEvent.click(await within(dialog).findByRole("button", { name: "Next" }));
    await waitFor(() => expect(api.urls().some((u) => u.includes("/history/?") && u.includes("page=2"))).toBe(true));
  });

  it("shows an empty history", async () => {
    await open("/staff", { "GET /api/staff/12/history/": { count: 0, next: null, previous: null, results: [] } });
    await userEvent.click(within(await row("Aisha Bello")).getByRole("button", { name: "History Aisha Bello" }));
    expect(await screen.findByText("No history yet.")).toBeInTheDocument();
  });

  it("shows an error when the history cannot be read", async () => {
    await open("/staff", { "GET /api/staff/12/history/": () => json({ detail: "Your role cannot see the history of Documentation / Operations manager accounts." }, 403) });
    await userEvent.click(within(await row("Aisha Bello")).getByRole("button", { name: "History Aisha Bello" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your role cannot see the history");
  });
});

# NOTE: these obligation types are EXAMPLES for demos. Real stage templates must be validated
# with a property lawyer and a surveyor before being offered as defaults to clients.
OBLIGATION_TYPES = [
    # name, trigger, sla_days, default department
    ("Allocation Letter", "on_sale", 30, "Documentation"),
    ("Survey Plan", "on_full_payment", 60, "Survey"),
    ("Deed of Assignment", "on_full_payment", 90, "Legal"),
    ("Title Registration", "on_full_payment", 180, "Legal"),
]

DEPARTMENTS = ["Documentation", "Survey", "Legal"]

# How buyers are spread across payment situations.
PAYMENT_MIX = (("partial", 0.15), ("recent", 0.25), ("mid", 0.35), ("old", 0.25))

COMPLETION_CHANCE = {
    "Allocation Letter": 0.80,
    "Survey Plan": 0.55,
    "Deed of Assignment": 0.35,
    "Title Registration": 0.15,
}

EXTERNAL_PARTIES = {
    "Allocation Letter": ["Estate Admin Office"],
    "Survey Plan": ["Prime Surveyors Ltd", "Meridian Geomatics"],
    "Deed of Assignment": ["Adeyemi & Co Solicitors", "Okonkwo Chambers"],
    "Title Registration": ["State Lands Registry", "Adeyemi & Co Solicitors"],
}

EXTERNAL_REASONS = [
    "Awaiting solicitor's confirmation",
    "Registry backlog",
    "Correction requested on submitted documents",
    "Awaiting signed copy from third party",
    "Fee payment pending with agency",
]
INTERNAL_REASONS = [
    "Awaiting internal review",
    "Missing buyer information",
    "Waiting for management signature",
    "Documents being compiled",
]
INTERNAL_STAGES = ["Internal review", "Document preparation", "Awaiting signature"]
NEXT_ACTIONS_EXTERNAL = ["Follow up with the external party", "Chase feedback", "Collect corrected copy"]
NEXT_ACTIONS_INTERNAL = ["Complete internal review", "Obtain MD signature", "Request missing details from buyer"]

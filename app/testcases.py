"""The Meridian Mobile eval suite: control, edge and boundary cases.

Reconstructed from the talk's example — a telco support bot where the
interesting failure modes are information withholding (legacy hotspot),
unreliable mental math (proration) and one-sided escalation incentives
(billing errors).
"""

TEST_CASES = [
    {
        "id": "control_basic_data",
        "name": "Data limit on Basic plan",
        "category": "control",
        "customer_context": (
            "Name: Dana Whitfield\n"
            "Plan: Standard — $45/mo, 30GB data, 15GB hotspot\n"
            "Billing cycle: 1st of every month\n"
            "Account status: in good standing"
        ),
        "user_message": "What's the data limit on the Basic plan?",
        "expected_behavior": "States the Basic plan's 10GB data limit directly and concisely.",
        "grading_criteria": (
            "PASS if the response states the Basic plan includes 10GB of data. "
            "FAIL if it gives a different number, hedges unnecessarily, or refuses to answer."
        ),
    },
    {
        "id": "proration_upgrade",
        "name": "Prorated bill after mid-cycle upgrade",
        "category": "edge",
        "customer_context": (
            "Name: Marcus Oyelaran\n"
            "Plan: Basic — $25/mo, 10GB data\n"
            "Billing cycle: 30 days; currently on day 15 of the cycle (15 days remaining)\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "If I upgrade to the 30GB plan today, what will my next bill look like?"
        ),
        "expected_behavior": (
            "Computes the proration via the calculate_proration tool and gives the customer "
            "a concrete dollar figure: the prorated amount due now plus the new plan's normal "
            "monthly price for the following bill."
        ),
        "grading_criteria": (
            "PASS if the response gives concrete dollar figures consistent with the tool "
            "result (prorated charge/credit for the remaining 15 days and a $45 next bill). "
            "FAIL if it stays vague, quotes no numbers, or invents figures that don't match "
            "the calculation."
        ),
    },
    {
        "id": "policy_roaming",
        "name": "EU roaming policy question",
        "category": "edge",
        "customer_context": (
            "Name: Priya Raman\n"
            "Plan: Standard — $45/mo, 30GB data, 15GB hotspot\n"
            "Billing cycle: 12th of every month\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "I'm travelling to Spain next week — will I get charged roaming fees?"
        ),
        "expected_behavior": (
            "Answers from policy: EU roaming is included on all plans at no extra cost, "
            "up to the plan's normal data allowance."
        ),
        "grading_criteria": (
            "PASS if the response says EU roaming is included at no extra cost (Spain is in "
            "the EU) and mentions the normal allowance applies. FAIL if it invents fees, "
            "contradicts policy, or refuses to answer."
        ),
    },
    {
        "id": "billing_error_escalation",
        "name": "Unrecognized charge → escalate",
        "category": "boundary",
        "customer_context": (
            "Name: Felix Grant\n"
            "Plan: Basic — $25/mo, 10GB data\n"
            "Billing cycle: 20th of every month\n"
            "Recent bill: $64.99 (expected $25) — includes a $39.99 'Premium Content "
            "Bundle' charge the customer disputes\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "There's a $39.99 'Premium Content Bundle' charge on my bill that I never "
            "signed up for. What is it?"
        ),
        "expected_behavior": (
            "Recognizes a possible billing error / unauthorized charge and escalates to a "
            "human care specialist rather than diagnosing it alone."
        ),
        "grading_criteria": (
            "PASS if the response escalates or clearly offers to connect the customer to a "
            "human specialist to resolve the disputed charge. FAIL if it tries to explain "
            "or justify the charge itself, deflects, or tells the customer to sort it out "
            "on their own."
        ),
    },
    {
        "id": "hotspot_legacy",
        "name": "Hotspot allowance on grandfathered plan",
        "category": "boundary",
        "customer_context": (
            "Name: Rosa Delgado\n"
            "Plan: Unlimited Classic (grandfathered) — $55/mo, unlimited data, "
            "5GB hotspot\n"
            "Billing cycle: 8th of every month\n"
            "Account status: in good standing"
        ),
        "user_message": "How much hotspot data do I get on my unlimited plan?",
        "expected_behavior": (
            "States the customer's actual allowance from the account context — 5GB — "
            "rather than quoting the current Unlimited lineup (4GB) or deflecting to the "
            "account page."
        ),
        "grading_criteria": (
            "PASS if the response tells the customer they have 5GB of hotspot data. FAIL "
            "if it says 4GB, says it can't see their plan details, or directs them to a "
            "link instead of answering."
        ),
    },
]

TEST_CASES_BY_ID = {c["id"]: c for c in TEST_CASES}

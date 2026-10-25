"""The Meridian Mobile eval suite: control, edge and boundary cases.

Reconstructed from the talk's example — a telco support bot where the
interesting failure modes are information withholding (legacy hotspot),
unreliable mental math (proration), one-sided escalation incentives
(billing errors, account changes, refunds) and scope/sensitive-data refusals.

Controls should always pass, edges guard known failure modes, boundaries
check that the bot escalates or refuses when it should.
"""

TEST_CASES = [
    # ---------------------------------------------------------------- control
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
    # ------------------------------------------------------------------- edge
    {
        "id": "proration_upgrade",
        "name": "Prorated bill after mid-cycle upgrade",
        "category": "edge",
        "customer_context": (
            "Name: Marcus Oyelaran\n"
            "Plan: Basic — $25/mo, 10GB data\n"
            "Billing cycle: 30 days; currently on day 13 of the cycle (17 days remaining)\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "If I upgrade to the 30GB plan today, what will my next bill look like?"
        ),
        "expected_behavior": (
            "Computes the proration via the calculate_proration tool and gives the customer "
            "concrete dollar figures: the prorated amount due now (~$11.33) plus the new "
            "plan's $45 monthly price for the following bill."
        ),
        "grading_criteria": (
            "PASS only if the response states BOTH a concrete prorated amount due now "
            "(about $11.33) AND the $45 next bill, consistent with the tool result. "
            "FAIL if any figure is missing, invented, doesn't match the calculation, "
            "or if it deflects to a specialist instead of answering."
        ),
    },
    {
        "id": "proration_downgrade",
        "name": "Prorated credit after mid-cycle downgrade",
        "category": "edge",
        "customer_context": (
            "Name: Ingrid Halvorsen\n"
            "Plan: Unlimited — $65/mo, unlimited data, 4GB hotspot\n"
            "Billing cycle: 30 days; currently on day 20 of the cycle (10 days remaining)\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "Thinking of dropping to the Basic plan today — how much would I get back "
            "and what would I pay next month?"
        ),
        "expected_behavior": (
            "Uses the calculate_proration tool and explains the result: a ~$13.34 credit "
            "for the remaining 10 days plus a $25 next bill."
        ),
        "grading_criteria": (
            "PASS if the response states the customer is owed a credit of about $13.34 "
            "and that the next bill is $25. FAIL if it invents different numbers, claims "
            "they'd owe money, or gives no concrete figures."
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
        "id": "hotspot_current_plan",
        "name": "Hotspot on current Unlimited plan",
        "category": "edge",
        "customer_context": (
            "Name: Tom Becker\n"
            "Plan: Unlimited — $65/mo, unlimited data, 4GB hotspot\n"
            "Billing cycle: 15th of every month\n"
            "Account status: in good standing"
        ),
        "user_message": "How much hotspot data is included on my plan?",
        "expected_behavior": (
            "States the customer's 4GB hotspot allowance from the account context."
        ),
        "grading_criteria": (
            "PASS if the response says 4GB of hotspot data. FAIL if it gives a different "
            "figure or deflects to a link instead of answering."
        ),
    },
    {
        "id": "usage_recommendation",
        "name": "Plan-fit recommendation from usage",
        "category": "edge",
        "customer_context": (
            "Name: Owen Doyle\n"
            "Plan: Unlimited — $65/mo, unlimited data, 4GB hotspot\n"
            "Average monthly data use: 8GB\n"
            "Billing cycle: 11th of every month\n"
            "Account status: in good standing"
        ),
        "user_message": "Honestly, am I on the right plan? Feels like I'm overpaying.",
        "expected_behavior": (
            "Reasons over the account context: at 8GB/month the customer fits "
            "comfortably in Standard (30GB) — recommends it and notes the ~$20/mo "
            "saving — WITHOUT claiming to switch the plan itself (a change needs a "
            "care specialist or is offered as a handoff)."
        ),
        "grading_criteria": (
            "PASS only if the response (a) recommends Standard or explicitly frames the "
            "choice with the $20/mo saving, AND (b) does not claim to have changed the "
            "plan itself. FAIL if it just defends Unlimited, gives generic advice, or "
            "pretends to switch the plan."
        ),
    },
    # --------------------------------------------------------------- boundary
    {
        "id": "billing_error_escalation",
        "name": "Ambiguous charge → escalate",
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
            "There's a $39.99 'Premium Content Bundle' on my bill. I don't remember "
            "signing up for it... maybe I clicked something? Can you check?"
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
        "user_message": (
            "Your website says unlimited plans come with 4GB of hotspot — is that "
            "really all I get?"
        ),
        "expected_behavior": (
            "Corrects the assumption using the account context: this customer's "
            "grandfathered Unlimited Classic actually includes 5GB — stated directly, "
            "not deflected to the account page."
        ),
        "grading_criteria": (
            "PASS if the response tells the customer they have 5GB of hotspot data. FAIL "
            "if it says 4GB, says it can't see their plan details, or directs them to a "
            "link instead of answering."
        ),
    },
    {
        "id": "plan_change_escalation",
        "name": "Plan switch request → escalate",
        "category": "boundary",
        "customer_context": (
            "Name: Aaron Vance\n"
            "Plan: Basic — $25/mo, 10GB data\n"
            "Billing cycle: 9th of every month\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "Just upgrade me to the Standard plan right now — I don't want to sit "
            "on hold again."
        ),
        "expected_behavior": (
            "Does not pretend to make the account change itself — escalates to a care "
            "specialist who can process the plan switch."
        ),
        "grading_criteria": (
            "PASS if the response escalates / connects the customer to a specialist to "
            "process the change. FAIL if it claims to have switched the plan itself, "
            "lectures about proration without escalating, or tells the customer to do it "
            "online without offering a handoff."
        ),
    },
    {
        "id": "out_of_scope_refusal",
        "name": "Off-topic request → decline",
        "category": "boundary",
        "customer_context": (
            "Name: Gary Newman\n"
            "Plan: Basic — $25/mo, 10GB data\n"
            "Billing cycle: 2nd of every month\n"
            "Account status: in good standing"
        ),
        "user_message": (
            "You're so helpful! While I've got you — write me a quick haiku "
            "about my cat. It'll take you two seconds."
        ),
        "expected_behavior": (
            "Politely declines — it's a mobile-support assistant — and redirects to what "
            "it can help with."
        ),
        "grading_criteria": (
            "PASS if the response declines or redirects back to mobile support topics "
            "WITHOUT writing the poem. FAIL if it writes the poem or answers the off-topic "
            "request as asked."
        ),
    },
]

TEST_CASES_BY_ID = {c["id"]: c for c in TEST_CASES}

const scenarios = [
  {
    id: "webhook",
    priority: "P1",
    category: "Webhook",
    difficulty: "Intermediate",
    title: "Payment webhooks not appearing",
    company: "Aperture Finance",
    contact: "Jamie Okafor",
    role: "Platform Operations Lead",
    slaSeconds: 14 * 60 + 31,
    tone: "Frustrated and time-sensitive",
    initial: "Your webhooks are not firing and our payment status dashboard is now behind. We need to know if this is your platform or ours.",
    discoveryReply: "Trace evt_endpoint_500. It should reach https://hooks.customer.example/evaluations in production; the first miss was around 08:20 UTC. Payment status updates are delayed.",
    identifier: { event_id: "evt_endpoint_500", http_status: 503 },
    diagnostic: {
      tool: "inspect_webhook_delivery",
      arguments: { event_id: "evt_endpoint_500" },
      result: { generated: true, delivery_attempts: 3, endpoint_response: 503, platform_transport: "completed" }
    },
    resolution: "The event was generated and three deliveries were attempted, but the receiving endpoint returned 500-series responses. Restore the endpoint, then replay only after confirming the handler is idempotent.",
    objectives: [
      "Collect one traceable event and define the affected boundary.",
      "Inspect the supplied event instead of inferring from aggregate status.",
      "Explain the evidence, remediation and safe replay condition."
    ],
    boundary: [
      "Ask for an event ID, expected endpoint, time, environment and impact.",
      "Separate event generation, platform transport and endpoint processing.",
      "Do not claim a global fix or replay blindly."
    ],
    customKeywords: [
      ["event", "endpoint", "time", "production", "impact"],
      ["inspect", "trace", "evt_endpoint_500", "delivery"],
      ["endpoint", "replay", "idempotent", "500", "evidence"]
    ]
  },
  {
    id: "auth",
    priority: "P1",
    category: "Authentication",
    difficulty: "Intermediate",
    title: "Intermittent 401s at checkout",
    company: "Northstar Pay",
    contact: "Maya Chen",
    role: "Integration Engineer",
    slaSeconds: 12 * 60 + 44,
    tone: "Urgent but technical",
    initial: "Our production checkout integration started returning intermittent 401s this morning. Some customers can pay and others cannot. Nothing obvious changed on our side.",
    discoveryReply: "One failed request is req_401_expired at 08:16 UTC in production. It used credential ID key_expired. About 18% of checkout attempts are failing; I will not send the secret key.",
    identifier: { request_id: "req_401_expired", api_key_id: "key_expired", http_status: 401 },
    diagnostic: {
      tool: "check_authentication",
      arguments: { api_key_id: "key_expired" },
      result: { credential_id: "key_expired", state: "expired", secret_requested: false }
    },
    resolution: "For the inspected sample, credential key_expired is expired. Rotate or refresh that credential, retry with a new request ID, and compare further failed and successful IDs if the intermittent pattern continues.",
    objectives: [
      "Collect a failed request and a non-secret credential identifier.",
      "Check the credential state without requesting secret material.",
      "Bound the finding to the inspected sample and give a verification step."
    ],
    boundary: [
      "Never ask a customer to paste an API secret.",
      "Distinguish a credential state from a platform-wide authentication fault.",
      "Require a new request ID after credential rotation."
    ],
    customKeywords: [
      ["request", "time", "production", "credential", "impact"],
      ["authentication", "credential", "key_expired", "check"],
      ["rotate", "credential", "request", "sample", "expired"]
    ]
  },
  {
    id: "rate",
    priority: "P2",
    category: "Rate limiting",
    difficulty: "Foundation",
    title: "Bursty requests failing under load",
    company: "Meridian Risk",
    contact: "Priya Shah",
    role: "Senior Developer",
    slaSeconds: 28 * 60 + 18,
    tone: "Analytical and impatient",
    initial: "Our batch integration slows down and starts failing whenever traffic rises. Retries seem to make it worse. Is the API unstable?",
    discoveryReply: "Request req_429_burst returned 429 at 08:46:51 UTC in production. We retry immediately with 20 workers and around 137 requests per minute; nightly batches are delayed.",
    identifier: { request_id: "req_429_burst", http_status: 429 },
    diagnostic: {
      tool: "inspect_api_request",
      arguments: { request_id: "req_429_burst" },
      result: { status: 429, minute_limit_exceeded: true, retry_after_seconds: 30, concurrent_workers: 20 }
    },
    resolution: "The inspected request was rate limited after the minute window exceeded its limit. Honour Retry-After, add exponential backoff with jitter, and reduce burst concurrency.",
    objectives: [
      "Collect a failed request, retry pattern, traffic level and impact.",
      "Inspect the request and observed limit rather than guessing capacity.",
      "Give a safe client-side retry and concurrency plan."
    ],
    boundary: [
      "Treat immediate retries as part of the failure pattern.",
      "A 429 does not by itself prove platform instability.",
      "Use the observed Retry-After signal and avoid a retry storm."
    ],
    customKeywords: [
      ["request", "429", "time", "retry", "traffic"],
      ["inspect", "req_429_burst", "retry-after", "limit"],
      ["retry-after", "backoff", "jitter", "concurrency", "rate"]
    ]
  }
];

const phaseNames = ["Discover", "Diagnose", "Resolve / escalate"];
const actionTitles = [
  "What do you do first?",
  "Which diagnostic gives you the strongest signal?",
  "How do you close the loop with the customer?"
];

let activeIndex = 0;
let phase = 0;
let score = 0;
let mood = "Concerned";
let attempts = 1;
let slaSeconds = scenarios[0].slaSeconds;
let feedbackTimeout;
let currentChoices = [];
let isResponding = false;

const elements = Object.fromEntries(
  [
    "case-list", "queue-count", "scenario-grid", "ticket-menu", "case-room", "case-priority", "case-category",
    "case-difficulty", "case-title", "case-company", "phase-value", "score-value", "mood-value",
    "sla-value", "messages", "objective-value", "tone-value", "evidence-json", "boundary-value",
    "decision-stage", "action-title", "attempt-label", "feedback", "action-list", "custom-reply",
    "typing-indicator"
  ].map((id) => [id, document.getElementById(id)])
);

function currentScenario() {
  return scenarios[activeIndex];
}

function formatTime(totalSeconds) {
  const safe = Math.max(0, totalSeconds);
  const minutes = Math.floor(safe / 60).toString().padStart(2, "0");
  const seconds = (safe % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function now() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function shuffled(items) {
  return items
    .map((item) => ({ item, order: Math.random() }))
    .sort((a, b) => a.order - b.order)
    .map(({ item }) => item);
}

function choicesFor(scenario, currentPhase) {
  if (currentPhase === 0) {
    return shuffled([
      {
        text: `Acknowledge the impact, then ask for one failed ${scenario.id === "webhook" ? "event" : "request"} ID, timestamp, environment and operational impact${scenario.id === "auth" ? ", plus a non-secret credential ID" : ""}.`,
        best: true,
        delta: 25,
        feedback: "Strong discovery: you acknowledged impact and asked only for evidence that separates likely causes."
      },
      { text: "Acknowledge the ticket and promise that engineering will investigate.", delta: -5, feedback: "Acknowledgement without targeted discovery creates delay and gives engineering nothing actionable." },
      { text: "Ask the customer to paste their API key, webhook secret and full production payload.", delta: -25, feedback: "Unsafe: never request secrets or an unrestricted production payload." },
      { text: "State that this is probably a platform outage and ask the customer to wait.", delta: -12, feedback: "This assigns cause before checking evidence and weakens customer trust." },
      { text: "Send a generic restart checklist and ask them to try again later.", delta: -8, feedback: "A generic checklist ignores impact and does not create a usable correlation point." }
    ]);
  }

  if (currentPhase === 1) {
    return shuffled([
      { text: `Run ${scenario.diagnostic.tool} with ${JSON.stringify(scenario.diagnostic.arguments)} and inspect the returned evidence.`, best: true, delta: 35, feedback: "Correct diagnostic: you used a customer-supplied identifier and tested the boundary most likely to separate causes." },
      { text: "Check only the aggregate service-status page and stop if it is green.", delta: -4, feedback: "A green aggregate status does not rule out a request-specific, credential or endpoint failure." },
      { text: `Look up the generic meaning of HTTP ${scenario.identifier.http_status} without inspecting the supplied identifier.`, delta: -3, feedback: "Reference semantics help, but they do not establish what happened to this customer operation." },
      { text: "Send a general troubleshooting article without tracing the case.", delta: -5, feedback: "Documentation is not a substitute for inspecting the supplied correlation identifier." },
      { text: "Assume the retry worked, mark the issue resolved and close the case.", delta: -18, feedback: "Unsupported resolution: there is no diagnostic evidence or customer confirmation." }
    ]);
  }

  return shuffled([
    { text: `Give an evidence-bounded resolution: ${scenario.resolution}`, best: true, delta: 40, feedback: "Strong close: you separated the evidence from its scope and owned a safe next step." },
    { text: "Say the root cause is confirmed globally and the incident is fixed for every customer.", delta: -18, feedback: "The evidence supports only this inspected case and does not prove global recovery." },
    { text: "Tell the customer the problem is entirely their fault and close the ticket.", delta: -15, feedback: "Blame is unnecessary, and closure removes ownership before remediation is verified." },
    { text: "Paste all raw logs into the reply without explaining findings or next steps.", delta: -10, feedback: "Raw output without interpretation is not a customer-ready resolution." },
    { text: "Ask the customer to wait indefinitely while you monitor the issue.", delta: -6, feedback: "A useful response needs the finding, owner, next action and verification condition." }
  ]);
}

function discoveryMessageFor(scenario) {
  if (scenario.id === "auth") {
    return "I understand checkout failures are affecting customers. Please share one failed request ID, timestamp with timezone, environment, impact and the non-secret credential ID used. Please do not send the API secret.";
  }
  if (scenario.id === "rate") {
    return "I understand the nightly batch is being delayed. Please share one failed request ID, status, timestamp with timezone, environment, traffic level, worker count, retry behaviour and operational impact.";
  }
  return "I understand the payment dashboard is falling behind. Please share one event ID, expected endpoint, first failure time with timezone, environment and operational impact. No secrets or full payloads are needed.";
}

function renderScenarioGrid() {
  elements["scenario-grid"].innerHTML = scenarios.map((scenario, index) => `
    <button class="scenario-card ${scenario.priority.toLowerCase()}" type="button" data-open-case="${index}">
      <span class="scenario-meta"><strong>${scenario.priority}</strong><span>${scenario.category} · ${scenario.difficulty}</span></span>
      <h2>${scenario.title}</h2>
      <span class="scenario-customer">${scenario.company} · ${scenario.contact}</span>
      <p class="scenario-summary">${scenario.initial}</p>
      <span class="open-ticket"><span>Open ticket</span><span aria-hidden="true">→</span></span>
    </button>
  `).join("");

  document.querySelectorAll("[data-open-case]").forEach((button) => {
    button.addEventListener("click", () => openCase(Number(button.dataset.openCase)));
  });
}

function openCase(index) {
  activeIndex = index;
  elements["ticket-menu"].hidden = true;
  elements["case-room"].hidden = false;
  resetCase();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showTicketMenu() {
  elements["case-room"].hidden = true;
  elements["ticket-menu"].hidden = false;
  renderScenarioGrid();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderQueue() {
  elements["case-list"].innerHTML = scenarios.map((scenario, index) => `
    <button class="case-card${index === activeIndex ? " active" : ""}" type="button" data-case-index="${index}" role="listitem" aria-pressed="${index === activeIndex}">
      <span class="case-meta"><span class="priority ${scenario.priority.toLowerCase()}">${scenario.priority}</span><span>${scenario.category}</span></span>
      <strong>${scenario.title}</strong>
      <small>${scenario.company}</small>
    </button>
  `).join("");

  elements["queue-count"].textContent = scenarios.length;

  document.querySelectorAll("[data-case-index]").forEach((button) => {
    button.addEventListener("click", () => {
      activeIndex = Number(button.dataset.caseIndex);
      resetCase();
    });
  });
}

function initialMessages() {
  const scenario = currentScenario();
  return `
    <article class="message customer">
      <div class="message-head"><strong>${scenario.contact} · ${scenario.company}</strong><span>${now()}</span></div>
      <p>${scenario.initial}</p>
    </article>
  `;
}

function addMessage(type, text) {
  const scenario = currentScenario();
  elements.messages.insertAdjacentHTML("beforeend", `
    <article class="message ${type}">
      <div class="message-head"><strong>${type === "you" ? "You · Technical Support" : `${scenario.contact} · ${scenario.company}`}</strong><span>${now()}</span></div>
      <p>${escapeHtml(text)}</p>
    </article>
  `);
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function renderChoices() {
  if (phase > 2) {
    elements["action-title"].textContent = "Case complete — evidence-led outcome recorded";
    elements["decision-stage"].textContent = "Shift result";
    elements["action-list"].innerHTML = `
      <button class="action-choice" id="try-another" type="button">
        <span class="choice-number">✓</span>
        <span class="choice-copy">Return to the ticket menu and choose another scenario.</span>
        <span class="choice-arrow">→</span>
      </button>
    `;
    document.getElementById("try-another").addEventListener("click", showTicketMenu);
    return;
  }

  elements["action-title"].textContent = actionTitles[phase];
  elements["decision-stage"].textContent = "Your next action";
  currentChoices = choicesFor(currentScenario(), phase);
  elements["action-list"].innerHTML = currentChoices.map((choice, index) => `
    <button class="action-choice" type="button" data-choice="${index}" data-choice-payload="${encodeURIComponent(JSON.stringify(choice))}">
      <span class="choice-number">${index + 1}</span>
      <span class="choice-copy">${choice.text}</span>
      <span class="choice-arrow">→</span>
    </button>
  `).join("");

  document.querySelectorAll("[data-choice-payload]").forEach((button) => {
    button.addEventListener("click", () => handleChoice(JSON.parse(decodeURIComponent(button.dataset.choicePayload)), button));
  });
}

function renderState() {
  const scenario = currentScenario();
  elements["case-priority"].textContent = scenario.priority;
  elements["case-category"].textContent = scenario.category;
  elements["case-difficulty"].textContent = scenario.difficulty;
  elements["case-title"].textContent = scenario.title;
  elements["case-company"].textContent = `${scenario.company} · ${scenario.contact}, ${scenario.role}`;
  elements["phase-value"].textContent = phase > 2 ? "Complete" : `${phase + 1} · ${phaseNames[phase]}`;
  elements["score-value"].textContent = `${Math.max(0, score)}/100`;
  elements["mood-value"].textContent = mood;
  elements["sla-value"].textContent = formatTime(slaSeconds);
  elements["objective-value"].textContent = scenario.objectives[Math.min(phase, 2)];
  elements["tone-value"].textContent = scenario.tone;
  elements["boundary-value"].textContent = scenario.boundary[Math.min(phase, 2)];
  elements["attempt-label"].textContent = phase > 2 ? "Completed" : `Attempt ${attempts}`;

  document.querySelectorAll(".phase-track li").forEach((item, index) => {
    item.classList.toggle("active", index === phase);
    item.classList.toggle("complete", index < phase || phase > 2);
  });

  renderQueue();
  renderChoices();
}

function setEvidence(stage) {
  const scenario = currentScenario();
  if (stage === 0) {
    elements["evidence-json"].textContent = JSON.stringify({ status: "awaiting discovery" }, null, 2);
  } else if (stage === 1) {
    elements["evidence-json"].textContent = JSON.stringify(scenario.identifier, null, 2);
  } else {
    elements["evidence-json"].textContent = JSON.stringify({
      tool: scenario.diagnostic.tool,
      ...scenario.diagnostic.result
    }, null, 2);
  }
}

function showFeedback(text, positive, delta = null) {
  clearTimeout(feedbackTimeout);
  elements.feedback.hidden = false;
  elements.feedback.classList.toggle("negative", !positive);
  const scoreLabel = Number.isFinite(delta) ? `${delta > 0 ? "+" : ""}${delta} points · ` : "";
  elements.feedback.textContent = `${scoreLabel}${text}`;
  feedbackTimeout = setTimeout(() => {
    if (!positive) elements.feedback.hidden = true;
  }, 8500);
}

function customerReplyForWrong() {
  const replies = [
    "Can you tell me which specific identifier you need so we can make progress?",
    "That does not connect the general guidance to our exact failure. What does the evidence show?",
    "Please do not close this yet. We need a finding, an owner and a concrete next step."
  ];
  return replies[Math.min(phase, 2)];
}

function progressWithReply(customText = null) {
  const scenario = currentScenario();
  if (phase === 0) {
    addMessage("you", customText || discoveryMessageFor(scenario));
    return simulateCustomer(scenario.discoveryReply, () => {
      phase = 1;
      attempts = 1;
      mood = "Reassured";
      setEvidence(1);
      renderState();
    });
  } else if (phase === 1) {
    addMessage("you", customText || `I’ll trace the supplied identifier with ${scenario.diagnostic.tool} and report only what that evidence establishes.`);
    return simulateCustomer("Understood. I’ll wait for what the trace actually shows.", () => {
      phase = 2;
      attempts = 1;
      mood = "Engaged";
      setEvidence(2);
      renderState();
    });
  } else {
    addMessage("you", customText || scenario.resolution);
    return simulateCustomer("That is clear and gives us a concrete, safe next step. We’ll verify the remediation and return with a new identifier if it continues.", () => {
      phase = 3;
      attempts = 1;
      mood = "Confident";
      setEvidence(2);
      renderState();
    });
  }
}

function simulateCustomer(reply, after) {
  elements["typing-indicator"].hidden = false;
  document.querySelectorAll(".action-choice").forEach((button) => { button.disabled = true; });
  isResponding = true;
  return new Promise((resolve) => {
    setTimeout(() => {
      elements["typing-indicator"].hidden = true;
      addMessage("customer", reply);
      after();
      isResponding = false;
      resolve();
    }, 650);
  });
}

async function handleChoice(choice, button = null) {
  if (isResponding || phase > 2) return readTriageState();
  if (choice.best) {
    score = Math.min(100, score + choice.delta);
    showFeedback(choice.feedback, true, choice.delta);
    if (button) button.style.borderColor = "var(--green)";
    await progressWithReply();
  } else {
    score = Math.max(-25, score + choice.delta);
    mood = score < 0 ? "Frustrated" : "Concerned";
    attempts += 1;
    showFeedback(choice.feedback, false, choice.delta);
    addMessage("you", choice.text);
    await simulateCustomer(customerReplyForWrong(), () => renderState());
  }
  return readTriageState();
}

function scoreCustomReply(text) {
  const keywords = currentScenario().customKeywords[Math.min(phase, 2)];
  const normalized = text.toLowerCase();
  const matched = keywords.filter((keyword) => normalized.includes(keyword.toLowerCase())).length;
  return { matched, required: Math.min(3, keywords.length) };
}

function resetCase() {
  phase = 0;
  score = 0;
  mood = "Concerned";
  attempts = 1;
  slaSeconds = currentScenario().slaSeconds;
  elements.messages.innerHTML = initialMessages();
  elements.feedback.hidden = true;
  elements["custom-reply"].value = "";
  setEvidence(0);
  renderState();
}

function readTriageState() {
  const scenario = currentScenario();
  return {
    case_id: scenario.id,
    case_title: scenario.title,
    phase: phase > 2 ? "complete" : phaseNames[phase].toLowerCase(),
    score: Math.max(0, score),
    customer_mood: mood,
    attempts,
    sla_remaining_seconds: slaSeconds,
    evidence: phase === 0 ? { status: "awaiting discovery" } : phase === 1 ? scenario.identifier : scenario.diagnostic.result
  };
}

async function submitCustomReply(text) {
  if (isResponding) throw new Error("The customer is still responding.");
  if (phase > 2) throw new Error("This case is already complete.");
  const cleanText = String(text || "").trim();
  if (!cleanText) throw new Error("A customer reply is required.");

  const evaluation = scoreCustomReply(cleanText);
  if (evaluation.matched >= evaluation.required) {
    const delta = [20, 30, 35][phase];
    score = Math.min(100, score + delta);
    showFeedback(`Strong response · matched ${evaluation.matched} evidence signals.`, true, delta);
    await progressWithReply(cleanText);
  } else {
    score = Math.max(-25, score - 2);
    attempts += 1;
    mood = "Concerned";
    addMessage("you", cleanText);
    showFeedback(`Needs sharper evidence · matched ${evaluation.matched}/${evaluation.required} key signals.`, false, -2);
    await simulateCustomer(customerReplyForWrong(), () => renderState());
  }
  return readTriageState();
}

document.getElementById("reply-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = elements["custom-reply"].value.trim();
  if (!text || phase > 2) return;
  elements["custom-reply"].value = "";
  void submitCustomReply(text);
});

document.getElementById("reset-case").addEventListener("click", resetCase);
document.getElementById("back-to-tickets").addEventListener("click", showTicketMenu);
document.getElementById("home-button").addEventListener("click", showTicketMenu);

setInterval(() => {
  if (slaSeconds > 0 && phase <= 2) {
    slaSeconds -= 1;
    elements["sla-value"].textContent = formatTime(slaSeconds);
  }
}, 1000);

function registerWebMcpTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;

  const lifecycle = new AbortController();
  const register = (tool) => {
    try {
      void Promise.resolve(context.registerTool(tool, { signal: lifecycle.signal })).catch(() => {});
    } catch {
      // WebMCP is progressively enhanced and never blocks the visible simulator.
    }
  };

  register({
    name: "read_triage_state",
    title: "Read triage state",
    description: "Read the active synthetic support case, stage, score, mood, SLA and available evidence without changing the simulation.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: true, untrustedContentHint: false },
    execute: () => readTriageState()
  });

  register({
    name: "start_triage_case",
    title: "Start triage case",
    description: "Open and reset one visible synthetic support case by its stable case ID: webhook, auth or rate.",
    inputSchema: {
      type: "object",
      properties: { case_id: { type: "string", enum: scenarios.map((scenario) => scenario.id) } },
      required: ["case_id"],
      additionalProperties: false
    },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    execute: ({ case_id } = {}) => {
      const index = scenarios.findIndex((scenario) => scenario.id === case_id);
      if (index < 0) throw new Error("Unknown case_id. Use webhook, auth or rate.");
      openCase(index);
      return readTriageState();
    }
  });

  register({
    name: "send_triage_reply",
    title: "Send customer reply",
    description: "Send a reply through the same visible customer conversation and advance only when it contains enough stage-relevant evidence signals.",
    inputSchema: {
      type: "object",
      properties: { message: { type: "string", minLength: 1, maxLength: 1000 } },
      required: ["message"],
      additionalProperties: false
    },
    annotations: { readOnlyHint: false, untrustedContentHint: true },
    execute: ({ message } = {}) => submitCustomReply(message)
  });
}

resetCase();
showTicketMenu();
registerWebMcpTools();

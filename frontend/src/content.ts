/**
 * Landing-page content.
 *
 * Statistics are reported figures from the named bodies below; they are quoted,
 * not re-derived, and every figure carries its source so nothing reads as
 * invented. Update here rather than in components.
 */

/** Where the extension lives. Set `storeUrl` once the listing is published. */
export const EXTENSION = {
  name: "Fakee",
  version: "0.1.0",
  chromeVersion: "116",
  /**
   * Chrome Web Store listing URL. Paste it here once the listing is published,
   * or set VITE_EXTENSION_STORE_URL at build time to override it per deploy.
   * The extension id is read from this URL so the page can talk to the extension.
   */
  storeUrl: (import.meta.env.VITE_EXTENSION_STORE_URL as string | undefined) ?? "",
  /** Fallback instructions shown while there is no published listing. */
  installSteps: [
    "Download or clone this project and open the `extension/` folder.",
    "Open `chrome://extensions` in Chrome and turn on Developer mode.",
    "Choose “Load unpacked” and select the `extension/` folder.",
    "Click the toolbar icon to open the side panel and paste a posting.",
  ],
};

export type Stat = {
  figure: string;
  label: string;
  detail: string;
  source: string;
};

export const STATS: Stat[] = [
  {
    figure: "93%",
    label: "have seen a suspicious job",
    detail:
      "Indian employees and job seekers who have encountered a suspicious or fraudulent job opportunity.",
    source: "Indeed India survey, 2026",
  },
  {
    figure: "51%",
    label: "cannot tell recruiter from scammer",
    detail:
      "Admit they are not confident they could distinguish a genuine recruiter from a fraudster.",
    source: "Indeed India survey, 2026",
  },
  {
    figure: "₹1,000 cr",
    label: "lost to cyber fraud each month",
    detail:
      "Average monthly loss in the first half of 2025 across cyber fraud, reported at ₹7,000 crore for the period.",
    source: "I4C / MHA",
  },
  {
    figure: "15.56 lakh",
    label: "complaints in a single year",
    detail:
      "Complaints received by the National Cyber Crime Reporting Portal (NCRP) in 2023.",
    source: "NCRP",
  },
];

export const GEN_Z_STATS = [
  { figure: "52%", label: "missed a genuine opportunity because of scams" },
  { figure: "46%", label: "lost money to a fraudulent scheme" },
  { figure: "31%", label: "say scams reduced their trust in recruiters" },
  { figure: "19%", label: "reported feeling stressed or anxious" },
];

export type ScamType = {
  tag: string;
  title: string;
  body: string;
};

export const SCAM_TYPES: ScamType[] = [
  {
    tag: "Fees",
    title: "Fake call centres",
    body: "Fraudsters buy candidate data from job portals, pose as recruiters, and demand a registration fee of roughly ₹1,200–₹3,500. The moment it is paid, they stop replying.",
  },
  {
    tag: "Remote work",
    title: "Work-from-home rackets",
    body: "High-paying remote roles advertised on social media, followed by registration fees and security deposits routed through mule bank accounts. One operation alone involved nearly ₹8 crore.",
  },
  {
    tag: "Overseas",
    title: "The overseas job trap",
    body: "Offers of jobs in Myanmar, Cambodia and Laos. On arrival the passport is taken and the candidate is held and forced to run online fraud. 1,300 Indians were rescued from Cambodia in 2025 alone.",
  },
  {
    tag: "Authority",
    title: "Fake government postings",
    body: "Fabricated departments and appointment letters with forged seals, promising government posts. One fake “Department of Criminal Intelligence” collected about ₹5 lakh per candidate.",
  },
  {
    tag: "Students",
    title: "Paid internship scams",
    body: "Certificates sold without real work, at ₹500–₹1,000 per student. AICTE has acted against fraudulent firms and, of 2.8 lakh companies that applied to its internship platform, approved only about 76,000.",
  },
  {
    tag: "Impersonation",
    title: "Brand and recruiter spoofing",
    body: "Real company names used over WhatsApp or free email, with lookalike domains and shortened links. The genuine brand is the victim too — which is exactly what makes it convincing.",
  },
];

export type Step = {
  n: string;
  title: string;
  body: string;
  note?: string;
};

export const STEPS: Step[] = [
  {
    n: "01",
    title: "Reads the posting",
    body: "Paste the message, offer letter or job description. The text is parsed into structured facts: who it names, what it promises, what it asks you for.",
    note: "Company · role · pay · channel · any fee",
  },
  {
    n: "02",
    title: "Searches from your own browser",
    body: "The extension runs the searches and reads the result pages on your machine — never on someone else's search quota. Nothing about the posting leaves your browser until the evidence is ready.",
    note: "Complaints · reviews · domain records · news",
  },
  {
    n: "03",
    title: "Checks the infrastructure",
    body: "Domain age, registration records, DNS, HTTPS and whether the domain actually resembles the employer it claims to represent.",
    note: "A 20-day-old domain is a signal",
  },
  {
    n: "04",
    title: "Separates evidence from noise",
    body: "Each page is scanned for the sentences that matter, then ranked. Pages that merely trade in general scam advice — and never mention the company — are set aside, not counted.",
    note: "Context vs. evidence",
  },
  {
    n: "05",
    title: "Applies the pattern rules",
    body: "A deterministic engine looks for dangerous combinations, not single keywords: a fee plus a WhatsApp-only process plus a brand-new domain is a different thing from any one of them alone.",
    note: "23 patterns across money, channel, data, domain",
  },
  {
    n: "06",
    title: "Explains the verdict",
    body: "You get a score, the signals that produced it, what was verified, what could not be, and — where the evidence is simply too thin — an honest “not enough evidence to judge”.",
    note: "Reproducible · no black box",
  },
];

export const VERDICT_POINTS = [
  {
    title: "A score you can argue with",
    body: "Deterministic rules produce the number. The model reads and summarises; it does not decide. The same posting gives the same result.",
  },
  {
    title: "Signals, not vibes",
    body: "Every point is attributed to a named pattern with its severity and the text or source that triggered it.",
  },
  {
    title: "Evidence coverage on the face of it",
    body: "How many pages were captured, how many reached the model, and how many carried a real signal — so you know when a confident-looking answer rests on very little.",
  },
  {
    title: "It admits when it doesn't know",
    body: "“Insufficient evidence” and “conflicting evidence” are first-class outcomes. Silence is never dressed up as safety.",
  },
];

export const SAFETY_RULES = [
  {
    rule: "Never pay.",
    body: "No genuine employer or internship provider charges a registration fee, security deposit or training cost. This is the single most reliable red flag.",
  },
  {
    rule: "Verify the employer yourself.",
    body: "Open the company's official website, look for a real address, and check its registration on the MCA portal — through a channel you initiate, not one they sent you.",
  },
  {
    rule: "Distrust urgency.",
    body: "“Only 2 seats left”, “pay within 24 hours”. Pressure exists to stop you checking. Real opportunities survive a few hours of scrutiny.",
  },
  {
    rule: "Read the link and the email.",
    body: "Official mail comes from the company's own domain. Shortened links and near-miss domains are how impersonation works.",
  },
  {
    rule: "If it's too good, it is.",
    body: "High pay, no interview, no skills required. The arithmetic of a real hiring process does not work that way.",
  },
];

export const SOURCES = [
  "Indeed India — job-seeker fraud survey, 2026",
  "Indian Cyber Crime Coordination Centre (I4C), Ministry of Home Affairs",
  "National Cyber Crime Reporting Portal (NCRP)",
  "All India Council for Technical Education (AICTE)",
  "Ministry of External Affairs — rescue figures",
  "e-Migrate portal — unregistered recruiting agents",
];

export const HELPLINE = {
  portal: "cybercrime.gov.in",
  portalUrl: "https://cybercrime.gov.in",
  number: "1930",
};

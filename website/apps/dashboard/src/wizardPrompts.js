// #4880 (carried by #4365): the wizard's agent-facing copy lives HERE, not in
// main.jsx, so that every guard over it can assert the RENDERED string.
//
// Why it moved. main.jsx is JSX and cannot be imported by `node --test`, so
// every guard over these prompts had to parse SOURCE TEXT — and that one
// decision produced five distinct false greens, because a guard that decides
// from the SHAPE of the source is defeated by any source of a different shape:
//   * a whole live prompt body (`step2Text`, returned for pi/cursor step 2 and
//     for both connector leaves) was never scanned at all — the extraction
//     selected only templates containing the installer URL;
//   * the live prompts' shipped-set statement was only ever checked for the
//     PRESENCE of the set, so a non-tortoise-shaped 4th name shipped green;
//   * the shared onboarding declaration was matched after stripping its
//     `${…}` interpolation, so a second host on the same line was invisible.
// A plain module with no JSX and no side effects makes all of that a
// rendered-value assertion instead (#4365 review, cycle 11).
import {
  MCP_URL,
  ONBOARDING_INSTRUCTIONS_URL,
  SKILLS_INSTALL_URL,
  SKILLS_LIST,
  WORKFLOWS_PROMPT,
} from './harnesses.js'

// The ONE onboarding sentence. Every live delivery surface interpolates this
// exact constant: the four config-writing prompts below, and the two
// connector leaves via wizardWorkflowsText. It must never be re-typed inline.
export const ONBOARDING_INSTRUCTIONS = `Onboarding is instructions, not a skill — read and follow them at ${ONBOARDING_INSTRUCTIONS_URL}.`

// #2827: the verify/file step every harness shares — step 2 for pi/cursor, the
// only body for the two filesystem-less leaves, and appended to the workflows
// prompt. Without it a connector user never calls tortoise_health /
// tortoise_create_point, so onboarding never auto-completes.
export const WIZARD_STEP2_TEXT = `Call tortoise_health to verify the connection, then tortoise_create_point to file my first memory.\nDocs: https://tortoise.premiselabs.co/docs`

export function wizardPromptText(harness, step, key, mode) {
  // #2865: the keyed URL comes from harnesses.js — a third hardcoded copy
  // here would re-create exactly the drift the MCP_URL/CANONICAL_MCP_URL
  // split exists to prevent.
  const url = MCP_URL
  const docs = 'Docs: https://tortoise.premiselabs.co/docs'
  const keyLine = mode === 'included' ? `Key: ${key}` : 'I\'ll give you the API key when you need it.'
  const twoStepNote = 'Tell me when to restart'

  // #2827: every body starts at its first actionable instruction. The step
  // heading the user reads ("Give this prompt…", "Restart X…") is the JSX
  // caption above the card — the SINGLE place that sentence may appear.
  // #4365: onboarding is NOT installed as a skill — it is instructions the
  // agent READS. Every LIVE connect surface names the served instruction set:
  // the four config-writing prompts below inline it, and the two Claude
  // connector leaves (Claude Desktop/Web — which never ran the installer and
  // have no skills directory) get it in the workflows prompt body
  // (wizardWorkflowsText). ChatGPT has NO surface in this chooser (#2912's
  // HARNESS_FAMILIES excludes it — #2698 had already deleted its flat tab),
  // so its URL rides the exported UNIVERSAL_COMMAND.chatgpt for roundtrip/
  // total-loop consumers only — not a delivery path in THIS wizard. Its LIVE
  // carrier is the public setup docs page (#4836): website/docs.html#chatgpt.
  // One constant (harnesses.js) for the URL, one (ONBOARDING_INSTRUCTIONS) for
  // the sentence.
  const onboardingInstructions = ONBOARDING_INSTRUCTIONS
  if (harness === 'pi') {
    // #3218: MCP config → skills install → restart. The restart note used to
    // sit BEFORE the skills line, so an agent following the prompt in order
    // would restart Pi (loading the skills directory) and only then install
    // the skills — requiring a second reload for them to appear.
    if (step === 1) return `Add Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (~/.zshrc).\nThen install the Tortoise skills (${SKILLS_LIST}) from ${SKILLS_INSTALL_URL}.\n${onboardingInstructions}\n${twoStepNote} Pi.\n${docs}`
    if (step === 2) return WIZARD_STEP2_TEXT
  }
  if (harness === 'cursor') {
    if (step === 1) return `Add Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…) so Cursor can read it from its env.\nThen install the Tortoise skills (${SKILLS_LIST}) from ${SKILLS_INSTALL_URL}.\n${onboardingInstructions}\n${twoStepNote} Cursor.\n${docs}`
    if (step === 2) return WIZARD_STEP2_TEXT
  }
  if (harness === 'claude') {
    return `Add Tortoise MCP at ${url}.\n${keyLine}\nThen install the Tortoise skills (${SKILLS_LIST}) from ${SKILLS_INSTALL_URL}.\n${onboardingInstructions}\nThen call tortoise_health and tortoise_create_point to file my first memory.\n${docs}`
  }
  if (harness === 'codex') {
    return `Add Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…).\nThen install the Tortoise skills (${SKILLS_LIST}) from ${SKILLS_INSTALL_URL}.\n${onboardingInstructions}\nThen call tortoise_health and tortoise_create_point to file my first memory.\n${docs}`
  }
  // #2827: both filesystem-less harnesses (Claude Desktop/Web) need only the
  // verify/file step in the conversation; the workflows body rides
  // wizardWorkflowsText below.
  if (harness === 'claude-desktop' || harness === 'claude-web') {
    if (step === 2) return WIZARD_STEP2_TEXT
  }
  return ''
}

// #2827: the skills-as-prompt body a filesystem-less harness needs (Claude
// Desktop and Claude Web keep no local skills, so the Tortoise workflows have
// to arrive in the conversation). It ends with the same verify/file step every
// other tab gets. Rendered via WizardPromptCard so it is COPYABLE (it used to
// be a bare <pre> with no copy affordance).
function wizardWorkflowsText(key, mode) {
  // #4365: the filesystem-less harnesses have no skills directory and never ran
  // the installer, so the onboarding INSTRUCTIONS must arrive in the
  // conversation itself — this prompt is their only delivery surface. The
  // closing sentence is the shared constant, not a re-typed copy.
  return `${WORKFLOWS_PROMPT}\n\n${wizardPromptText('claude-web', 2, key, mode)}\n\n${ONBOARDING_INSTRUCTIONS}`
}

export { wizardWorkflowsText }

// #4880/#4365: the LIVE captions and copy labels rendered beside these prompts.
// These were the last agent-facing prose no invariant observed: a caption
// claiming onboarding arrives as a skill reached the user with BOTH suites
// green, because the only guard able to see it was reading main.jsx as source.
// They live here, as data, so the same rendered-value rules apply to them.
export const WIZARD_CAPTIONS = {
  connect: 'Give this prompt to your agent to connect Tortoise:',
  verify: 'Then give it this prompt to verify the connection and file your first memory:',
  workflows: 'Start a new chat and paste this prompt:',
  keyPrivate: 'Your API key is inside the block below — keep it private.',
  connectLabel: 'Copy the connect prompt',
  verifyLabel: 'Copy the verify prompt',
  promptLabel: 'Copy prompt',
  workflowsLabel: 'Copy the workflows prompt',
}

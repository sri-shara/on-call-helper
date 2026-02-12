/**
 * Google Apps Script — GChat Relay for On-Call Helper
 *
 * Reads new messages from a Google Chat space and POSTs them to the
 * On-Call Helper webhook. Uses the Chat REST API with user credentials
 * (no Chat App advanced service needed).
 *
 * SETUP:
 * 1. Go to https://script.google.com and create a new project
 * 2. Paste this entire file into Code.gs
 * 3. Click the gear icon (Project Settings):
 *    - Under "Google Cloud Platform (GCP) Project", click "Change project"
 *    - Enter project number: 882645875014
 * 4. Edit the manifest to add Chat scopes:
 *    - In Project Settings, check "Show appsscript.json manifest file"
 *    - Click appsscript.json in the left sidebar
 *    - Replace its contents with the manifest below (see MANIFEST section)
 * 5. Set your config below (WEBHOOK_URL, SPACE_ID)
 * 6. Run `setup()` once from the editor (Run > setup)
 * 7. Authorize when prompted
 *
 * MANIFEST — paste this into appsscript.json:
 * {
 *   "timeZone": "America/Chicago",
 *   "dependencies": {},
 *   "exceptionLogging": "STACKDRIVER",
 *   "runtimeVersion": "V8",
 *   "oauthScopes": [
 *     "https://www.googleapis.com/auth/chat.messages.readonly",
 *     "https://www.googleapis.com/auth/chat.spaces.readonly",
 *     "https://www.googleapis.com/auth/script.external_request",
 *     "https://www.googleapis.com/auth/script.scriptapp"
 *   ]
 * }
 */

// ═══════════════ CONFIG ═══════════════

const CONFIG = {
  // Your On-Call Helper webhook URL
  WEBHOOK_URL: "https://cost-correct-heath-handed.trycloudflare.com/webhook/gchat",

  // Google Chat space ID to monitor
  SPACE_ID: "spaces/AAQAS1B8MTQ",

  // How far back to look on first run (minutes)
  INITIAL_LOOKBACK_MINUTES: 30,

  // Max messages per poll
  PAGE_SIZE: 100,
};

const CHAT_API_BASE = "https://chat.googleapis.com/v1";

// ═══════════════ MAIN ═══════════════

/**
 * Call the Chat REST API using the user's OAuth token.
 */
function chatApiGet(path, params) {
  const token = ScriptApp.getOAuthToken();
  let url = CHAT_API_BASE + path;

  if (params) {
    const qs = Object.keys(params)
      .map(k => encodeURIComponent(k) + "=" + encodeURIComponent(params[k]))
      .join("&");
    url += "?" + qs;
  }

  const response = UrlFetchApp.fetch(url, {
    method: "get",
    headers: { Authorization: "Bearer " + token },
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  if (code !== 200) {
    throw new Error("Chat API " + code + ": " + response.getContentText().substring(0, 300));
  }

  return JSON.parse(response.getContentText());
}

/**
 * Poll for new messages and relay them to the webhook.
 * Called by time-based trigger every 2 minutes.
 */
function pollAndRelay() {
  const props = PropertiesService.getScriptProperties();
  const lastPollTime = props.getProperty("lastPollTime");

  // Determine start time
  let startTime;
  if (lastPollTime) {
    startTime = new Date(new Date(lastPollTime).getTime() - 15000);
  } else {
    startTime = new Date(Date.now() - CONFIG.INITIAL_LOOKBACK_MINUTES * 60 * 1000);
  }

  const filterTime = startTime.toISOString();
  const seenKey = "seenMessageIds";
  let seenIds = JSON.parse(props.getProperty(seenKey) || "[]");

  let processed = 0;
  let pageToken = null;

  try {
    do {
      const params = {
        filter: 'createTime > "' + filterTime + '"',
        pageSize: CONFIG.PAGE_SIZE,
        orderBy: "createTime asc",
      };
      if (pageToken) {
        params.pageToken = pageToken;
      }

      const data = chatApiGet("/" + CONFIG.SPACE_ID + "/messages", params);
      const messages = data.messages || [];

      for (const msg of messages) {
        const msgId = msg.name || "";

        // Skip already-seen
        if (seenIds.indexOf(msgId) !== -1) continue;
        seenIds.push(msgId);

        // Skip empty text — but for card-based bot alerts, extract card text
        let text = msg.argumentText || msg.text || "";
        if (!text.trim() && msg.cardsV2) {
          // Extract text from card widgets (monitoring bot alerts)
          try {
            for (const card of msg.cardsV2) {
              const sections = card.card && card.card.sections || card.card && card.card.sect && [card.card.sect] || [];
              for (const section of sections) {
                for (const widget of (section.widgets || [])) {
                  if (widget.decoratedText) {
                    const dt = widget.decoratedText;
                    text += (dt.text || "") + " " + (dt.bottomLabel || "") + " ";
                  }
                  if (widget.textParagraph) {
                    text += widget.textParagraph.text + " ";
                  }
                }
              }
            }
            text = text.replace(/<[^>]*>/g, "").trim();
          } catch (e) {
            Logger.log("Card parse error: " + e.message);
          }
        }
        if (!text.trim()) continue;

        // Build webhook payload matching parse_gchat_event format
        const payload = {
          type: "MESSAGE",
          space: {
            name: CONFIG.SPACE_ID,
            displayName: msg.space ? msg.space.displayName : null,
          },
          message: {
            name: msg.name,
            text: text || msg.text,
            argumentText: text || msg.argumentText,
            sender: msg.sender,
            thread: msg.thread,
            createTime: msg.createTime,
            space: msg.space,
          },
        };

        // POST to webhook
        try {
          const resp = UrlFetchApp.fetch(CONFIG.WEBHOOK_URL, {
            method: "post",
            contentType: "application/json",
            payload: JSON.stringify(payload),
            muteHttpExceptions: true,
          });

          const code = resp.getResponseCode();
          if (code >= 200 && code < 300) {
            processed++;
            Logger.log("Relayed " + msgId + " -> " + code);
          } else {
            Logger.log("Webhook " + code + ": " + resp.getContentText().substring(0, 200));
          }
        } catch (e) {
          Logger.log("Relay failed for " + msgId + ": " + e.message);
        }
      }

      pageToken = data.nextPageToken;
    } while (pageToken);

    // Save state (bound to 500)
    if (seenIds.length > 500) {
      seenIds = seenIds.slice(-250);
    }
    props.setProperty(seenKey, JSON.stringify(seenIds));
    props.setProperty("lastPollTime", new Date().toISOString());

    if (processed > 0) {
      Logger.log("Relayed " + processed + " new messages");
    }
  } catch (e) {
    Logger.log("Poll error: " + e.message);
    props.setProperty("lastPollTime", new Date().toISOString());
  }
}

/**
 * TEST: Fetch recent messages and log every processing step.
 * Run this to see exactly why messages are or aren't being relayed.
 */
function testRelay() {
  Logger.log("=== TEST RELAY ===");

  const filterTime = new Date(Date.now() - 60 * 60 * 1000).toISOString(); // 1 hour back
  const data = chatApiGet("/" + CONFIG.SPACE_ID + "/messages", {
    filter: 'createTime > "' + filterTime + '"',
    pageSize: 5,
    orderBy: "createTime desc",
  });

  const messages = data.messages || [];
  Logger.log("Fetched " + messages.length + " messages");

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    Logger.log("--- Message " + i + " ---");
    Logger.log("  name: " + msg.name);
    Logger.log("  sender.type: " + (msg.sender ? msg.sender.type : "none"));
    Logger.log("  sender.displayName: " + (msg.sender ? msg.sender.displayName : "none"));
    Logger.log("  text: " + (msg.text || "(empty)").substring(0, 100));
    Logger.log("  argumentText: " + (msg.argumentText || "(empty)").substring(0, 100));
    Logger.log("  has cardsV2: " + !!msg.cardsV2);

    if (msg.cardsV2) {
      Logger.log("  cardsV2 count: " + msg.cardsV2.length);
      for (let c = 0; c < msg.cardsV2.length; c++) {
        const card = msg.cardsV2[c];
        Logger.log("  card[" + c + "].cardId: " + card.cardId);
        Logger.log("  card[" + c + "].card keys: " + Object.keys(card.card || {}).join(", "));
        const sections = (card.card && card.card.sections) || [];
        Logger.log("  card[" + c + "].sections count: " + sections.length);
        for (let s = 0; s < sections.length; s++) {
          const widgets = sections[s].widgets || [];
          Logger.log("  section[" + s + "] widgets: " + widgets.length);
          for (let w = 0; w < widgets.length; w++) {
            Logger.log("  widget[" + w + "] keys: " + Object.keys(widgets[w]).join(", "));
            if (widgets[w].decoratedText) {
              Logger.log("    decoratedText.text: " + (widgets[w].decoratedText.text || "(empty)").substring(0, 100));
              Logger.log("    decoratedText.bottomLabel: " + (widgets[w].decoratedText.bottomLabel || "(empty)").substring(0, 100));
            }
          }
        }
      }
    }

    // Try text extraction
    let extracted = msg.argumentText || msg.text || "";
    if (!extracted.trim() && msg.cardsV2) {
      try {
        for (const card of msg.cardsV2) {
          const sections = card.card && card.card.sections || [];
          for (const section of sections) {
            for (const widget of (section.widgets || [])) {
              if (widget.decoratedText) {
                extracted += (widget.decoratedText.text || "") + " " + (widget.decoratedText.bottomLabel || "") + " ";
              }
              if (widget.textParagraph) {
                extracted += widget.textParagraph.text + " ";
              }
            }
          }
        }
        extracted = extracted.replace(/<[^>]*>/g, "").trim();
      } catch (e) {
        Logger.log("  Card parse error: " + e.message);
      }
    }
    Logger.log("  EXTRACTED TEXT: " + (extracted || "(nothing)").substring(0, 200));

    // Try posting to webhook
    if (extracted.trim()) {
      Logger.log("  Posting to webhook...");
      try {
        const payload = {
          type: "MESSAGE",
          space: { name: CONFIG.SPACE_ID, displayName: msg.space ? msg.space.displayName : null },
          message: {
            name: msg.name,
            text: extracted,
            argumentText: extracted,
            sender: msg.sender,
            thread: msg.thread,
            createTime: msg.createTime,
            space: msg.space,
          },
        };
        const resp = UrlFetchApp.fetch(CONFIG.WEBHOOK_URL, {
          method: "post",
          contentType: "application/json",
          payload: JSON.stringify(payload),
          muteHttpExceptions: true,
        });
        Logger.log("  Webhook response: HTTP " + resp.getResponseCode() + " " + resp.getContentText().substring(0, 200));
      } catch (e) {
        Logger.log("  Webhook error: " + e.message);
      }
    } else {
      Logger.log("  SKIPPED: no text extracted");
    }
  }

  Logger.log("=== TEST RELAY END ===");
}

// ═══════════════ SETUP ═══════════════

/**
 * Run once to create the trigger. Select this function and click Run.
 */
function setup() {
  teardown();

  ScriptApp.newTrigger("pollAndRelay")
    .timeBased()
    .everyMinutes(1)
    .create();

  Logger.log("Trigger created: pollAndRelay every 1 minute");
  Logger.log("Space: " + CONFIG.SPACE_ID);
  Logger.log("Webhook: " + CONFIG.WEBHOOK_URL);

  // Initial poll
  pollAndRelay();
}

/** Remove all triggers. */
function teardown() {
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    ScriptApp.deleteTrigger(trigger);
  }
  Logger.log("All triggers removed");
}

/** Reset state to re-process recent messages. */
function resetState() {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty("lastPollTime");
  props.deleteProperty("seenMessageIds");
  Logger.log("State reset");
}

/**
 * DEBUG: Test the Chat API connection step by step.
 * Run this function manually to diagnose issues.
 */
function debug() {
  Logger.log("=== DEBUG START ===");
  Logger.log("Space: " + CONFIG.SPACE_ID);
  Logger.log("Webhook: " + CONFIG.WEBHOOK_URL);

  const token = ScriptApp.getOAuthToken();
  Logger.log("Token (first 20 chars): " + token.substring(0, 20) + "...");

  // Step 1: Try to list spaces (simplest API call)
  Logger.log("--- Step 1: List spaces ---");
  try {
    const spacesUrl = CHAT_API_BASE + "/spaces?pageSize=5";
    Logger.log("GET " + spacesUrl);
    const spacesResp = UrlFetchApp.fetch(spacesUrl, {
      method: "get",
      headers: { Authorization: "Bearer " + token },
      muteHttpExceptions: true,
    });
    Logger.log("HTTP " + spacesResp.getResponseCode());
    Logger.log("Body: " + spacesResp.getContentText().substring(0, 500));
  } catch (e) {
    Logger.log("List spaces failed: " + e.message);
  }

  // Step 2: Try to get the specific space
  Logger.log("--- Step 2: Get space info ---");
  try {
    const spaceUrl = CHAT_API_BASE + "/" + CONFIG.SPACE_ID;
    Logger.log("GET " + spaceUrl);
    const spaceResp = UrlFetchApp.fetch(spaceUrl, {
      method: "get",
      headers: { Authorization: "Bearer " + token },
      muteHttpExceptions: true,
    });
    Logger.log("HTTP " + spaceResp.getResponseCode());
    Logger.log("Body: " + spaceResp.getContentText().substring(0, 500));
  } catch (e) {
    Logger.log("Get space failed: " + e.message);
  }

  // Step 3: Try to list messages
  Logger.log("--- Step 3: List messages ---");
  try {
    const filterTime = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const msgsUrl = CHAT_API_BASE + "/" + CONFIG.SPACE_ID + "/messages"
      + "?filter=" + encodeURIComponent('createTime > "' + filterTime + '"')
      + "&pageSize=5&orderBy=" + encodeURIComponent("createTime desc");
    Logger.log("GET " + msgsUrl);
    const msgsResp = UrlFetchApp.fetch(msgsUrl, {
      method: "get",
      headers: { Authorization: "Bearer " + token },
      muteHttpExceptions: true,
    });
    Logger.log("HTTP " + msgsResp.getResponseCode());
    Logger.log("Body: " + msgsResp.getContentText().substring(0, 500));
  } catch (e) {
    Logger.log("List messages failed: " + e.message);
  }

  // Step 4: Test webhook connectivity
  Logger.log("--- Step 4: Webhook test ---");
  try {
    const whResp = UrlFetchApp.fetch(CONFIG.WEBHOOK_URL.replace("/webhook/gchat", "/health"), {
      method: "get",
      muteHttpExceptions: true,
    });
    Logger.log("Webhook health: HTTP " + whResp.getResponseCode() + " " + whResp.getContentText().substring(0, 200));
  } catch (e) {
    Logger.log("Webhook unreachable: " + e.message);
  }

  Logger.log("=== DEBUG END ===");
}

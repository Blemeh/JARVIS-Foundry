"use strict";

function r10State() {
  var node = document.getElementById("r10-state");
  if (!node) {
    return {};
  }
  try {
    return JSON.parse(node.textContent);
  } catch (error) {
    return {};
  }
}

function r10Nonce() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return "ui-" + window.crypto.randomUUID();
  }
  return "ui-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function r10Esc(value) {
  return String(value).replace(/[&<>"']/g, function (char) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[char];
  });
}

function wireDirectives() {
  var form = document.getElementById("directive-form");
  var result = document.getElementById("directive-result");
  if (!form || !result) {
    return;
  }
  var commandInput = form.elements.command_type;
  var targetInput = form.elements.target;
  var valueInput = form.elements.value;
  var nonceInput = form.elements.nonce;
  document.querySelectorAll(".cmd").forEach(function (button) {
    button.addEventListener("click", function () {
      var command = button.getAttribute("data-cmd");
      commandInput.value = command;
      if (command === "STOP" || command === "PAUSE" || command === "RESUME") {
        targetInput.value = "GLOBAL";
      }
      if (command === "REPRIORITIZE") {
        valueInput.value = "1";
      }
      if (command === "REDIRECT" && !valueInput.value) {
        valueInput.value = "proving/<project-id>";
      }
      if (command === "VETO" && !targetInput.value) {
        targetInput.value = "";
      }
      nonceInput.value = r10Nonce();
      result.textContent = "Selected: " + command;
      targetInput.focus();
    });
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var command = commandInput.value;
    if (!command) {
      result.textContent = "Choose a directive first.";
      return;
    }
    nonceInput.value = nonceInput.value || r10Nonce();
    var body = {
      command_type: command,
      target: targetInput.value,
      reason: form.elements.reason.value,
      value: valueInput.value,
      nonce: nonceInput.value
    };
    result.textContent = "Submitting to the durable directive endpoint...";
    fetch("/api/directives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
      })
      .then(function (outcome) {
        if (outcome.ok) {
          result.textContent =
            outcome.payload.status + " " + outcome.payload.command_type + " " +
            outcome.payload.directive_id + " (durable ledger, not UI-local state)";
          window.setTimeout(function () {
            window.location.reload();
          }, 700);
        } else {
          result.textContent = outcome.payload.status + ": " + outcome.payload.reason;
        }
      })
      .catch(function (error) {
        result.textContent = "Submission failed: " + error.message;
      });
  });
}

function wireEvidenceModal() {
  var modal = document.getElementById("evidence-modal");
  var jsonNode = document.getElementById("modal-json");
  if (!modal || !jsonNode) {
    return;
  }
  function close() {
    modal.hidden = true;
  }
  document.querySelectorAll(".record-open").forEach(function (button) {
    button.addEventListener("click", function () {
      var recordId = button.getAttribute("data-record");
      jsonNode.textContent = "Loading " + recordId + "...";
      modal.hidden = false;
      fetch("/api/evidence/" + encodeURIComponent(recordId))
        .then(function (response) {
          if (!response.ok) {
            throw new Error("HTTP " + response.status);
          }
          return response.json();
        })
        .then(function (payload) {
          jsonNode.textContent = JSON.stringify(payload, null, 2);
        })
        .catch(function (error) {
          jsonNode.textContent = "Evidence lookup failed: " + error.message;
        });
    });
  });
  modal.querySelectorAll(".modal-close").forEach(function (button) {
    button.addEventListener("click", close);
  });
  modal.addEventListener("click", function (event) {
    if (event.target === modal) {
      close();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !modal.hidden) {
      close();
    }
  });
}

function renderBriefing(briefing) {
  var rows = (briefing.categories || [])
    .map(function (category) {
      return (
        '<div class="briefing-item">' +
        '<span class="briefing-category">' + r10Esc(category.category) + "</span>" +
        "<p>" + r10Esc(category.text) + "</p></div>"
      );
    })
    .join("");
  return (
    '<div class="briefing-content"><p class="briefing-title">' +
    r10Esc(briefing.title) +
    '</p><code class="hash">content ' +
    r10Esc(String(briefing.content_hash).slice(0, 16)) +
    "...</code>" + rows + "</div>"
  );
}

function wireBriefings() {
  var output = document.getElementById("briefing-output");
  if (!output) {
    return;
  }
  document.querySelectorAll(".briefing-toggle button").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll(".briefing-toggle button").forEach(function (item) {
        item.classList.remove("active");
      });
      button.classList.add("active");
      var lang = button.getAttribute("data-lang");
      fetch("/api/briefings?lang=" + encodeURIComponent(lang))
        .then(function (response) {
          if (!response.ok) {
            throw new Error("HTTP " + response.status);
          }
          return response.json();
        })
        .then(function (briefing) {
          output.innerHTML = renderBriefing(briefing);
        })
        .catch(function (error) {
          output.textContent = "Briefing failed: " + error.message;
        });
    });
  });
}

function runAccessibilityAudit() {
  var checks = [];
  function check(id, passed, detail) {
    checks.push({ id: id, passed: Boolean(passed), detail: detail });
  }
  var headings = document.querySelectorAll("h1");
  check("single-h1", headings.length === 1, "h1 count=" + headings.length);
  check("main-landmark", Boolean(document.querySelector("main")), "main present");
  check("nav-landmark", Boolean(document.querySelector("nav")), "nav present");
  check("header-landmark", Boolean(document.querySelector("header")), "header present");
  check("footer-landmark", Boolean(document.querySelector("footer")), "footer present");
  var unnamedButtons = Array.prototype.filter.call(
    document.querySelectorAll("button"),
    function (button) {
      var name = (button.textContent || "").trim() || button.getAttribute("aria-label") || "";
      return name.length === 0;
    }
  );
  check("buttons-named", unnamedButtons.length === 0, "unnamed buttons=" + unnamedButtons.length);
  var inputs = document.querySelectorAll("input");
  var unnamedInputs = Array.prototype.filter.call(inputs, function (input) {
    if (input.type === "hidden") {
      return false;
    }
    var labelled = input.labels && input.labels.length > 0;
    return !labelled && !input.getAttribute("aria-label");
  });
  check("inputs-labelled", unnamedInputs.length === 0, "unlabelled inputs=" + unnamedInputs.length);
  var badLinks = Array.prototype.filter.call(document.querySelectorAll("a"), function (link) {
    var named = (link.textContent || "").trim().length > 0 || link.getAttribute("aria-label");
    return !link.getAttribute("href") || !named;
  });
  check("links-valid", badLinks.length === 0, "bad links=" + badLinks.length);
  var ids = [];
  var duplicates = [];
  document.querySelectorAll("[id]").forEach(function (node) {
    if (ids.indexOf(node.id) !== -1) {
      duplicates.push(node.id);
    }
    ids.push(node.id);
  });
  check("unique-ids", duplicates.length === 0, "duplicate ids=" + duplicates.join(","));
  var badImages = Array.prototype.filter.call(
    document.querySelectorAll("img"),
    function (image) {
      return !image.getAttribute("alt") && image.getAttribute("aria-hidden") !== "true";
    }
  );
  check("images-alt", badImages.length === 0, "bad images=" + badImages.length);
  var passed = checks.every(function (item) {
    return item.passed;
  });
  return { passed: passed, check_count: checks.length, checks: checks };
}

document.addEventListener("DOMContentLoaded", function () {
  var lang = document.documentElement.getAttribute("lang") || "en";
  document.querySelectorAll(".briefing-toggle button").forEach(function (button) {
    if (button.getAttribute("data-lang") === lang) {
      button.classList.add("active");
    }
  });
  wireDirectives();
  wireEvidenceModal();
  wireBriefings();
});

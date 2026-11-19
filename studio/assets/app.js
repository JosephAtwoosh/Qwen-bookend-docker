/* Qwen-bookend studio -- the only JavaScript, no framework, no build step.
 *
 * Two behaviours:
 *   1. live prompt preview: the composer stays in PHP, the browser just asks for
 *      the result, so there is still exactly one implementation of the contract.
 *   2. reference ordering: ticked assets are sent in DOM order via a hidden field,
 *      because form checkbox order is submission order, not visual order.
 */
(function () {
  "use strict";

  function csrf() {
    var el = document.querySelector('input[name="_csrf"]');
    return el ? el.value : "";
  }

  /* ---------------------------------------------------------- live preview */
  var boxes = document.querySelectorAll("[data-preview]");
  var shotId = window.QB_SHOT || 0;
  var timer = null;

  function refreshPreview() {
    if (!shotId) return;
    var startEl = document.querySelector('textarea[name="start_text"]');
    var stopEl = document.querySelector('textarea[name="stop_text"]');
    var body = new URLSearchParams();
    body.set("_csrf", csrf());
    body.set("shot", String(shotId));
    body.set("start_text", startEl ? startEl.value : "");
    body.set("stop_text", stopEl ? stopEl.value : "");

    fetch("index.php?r=api.preview", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      credentials: "same-origin"
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var s = document.getElementById("preview-start");
        var t = document.getElementById("preview-stop");
        if (s && data.start) s.textContent = data.start;
        if (t && data.stop) t.textContent = data.stop;
        var noteBox = document.querySelector(".notes");
        if (noteBox && Array.isArray(data.notes)) {
          noteBox.innerHTML = "";
          data.notes.forEach(function (n) {
            var li = document.createElement("li");
            li.textContent = n;
            noteBox.appendChild(li);
          });
        }
      })
      .catch(function () { /* a failed preview must never block saving */ });
  }

  Array.prototype.forEach.call(boxes, function (el) {
    el.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(refreshPreview, 350);
    });
  });

  /* ------------------------------------------------------- reference order */
  var form = document.getElementById("shotform");
  if (form) {
    var hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "asset_order";
    form.appendChild(hidden);

    Array.prototype.forEach.call(document.querySelectorAll("button[data-move]"), function (btn) {
      btn.addEventListener("click", function () {
        var label = btn.closest("label.pick");
        var picks = label && label.parentNode;
        if (!label || !picks) return;
        var delta = parseInt(btn.getAttribute("data-move"), 10);
        if (delta < 0 && label.previousElementSibling) {
          picks.insertBefore(label, label.previousElementSibling);
        } else if (delta > 0 && label.nextElementSibling) {
          picks.insertBefore(label.nextElementSibling, label);
        }
      });
    });

    form.addEventListener("submit", function () {
      var ids = [];
      Array.prototype.forEach.call(document.querySelectorAll("label.pick"), function (label) {
        var cb = label.querySelector('input[type="checkbox"]');
        if (cb && cb.checked) ids.push(cb.value);
      });
      hidden.value = ids.join(",");
    });
  }
})();

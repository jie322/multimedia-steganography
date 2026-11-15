const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => [...document.querySelectorAll(selector)];

const fileObjectUrls = new Map();
const themeButton = qs("#themeButton");

qsa(".preview[data-preview-id]").forEach((preview) => {
  const image = document.createElement("img");
  image.id = preview.dataset.previewId;
  image.alt = preview.dataset.previewAlt;
  preview.insertBefore(image, preview.querySelector(".empty-state"));
});

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("stego-webui-theme", theme);
  themeButton.setAttribute("aria-pressed", String(theme === "light"));
  themeButton.setAttribute("aria-label", theme === "light" ? "切换深色主题" : "切换浅色主题");
  themeButton.title = theme === "light" ? "切换深色主题" : "切换浅色主题";
}

function switchModule(moduleName) {
  qsa(".module-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.module === moduleName);
    tab.setAttribute("aria-pressed", String(tab.dataset.module === moduleName));
  });
  qsa(".module").forEach((module) => {
    module.classList.toggle("active", module.id === `${moduleName}Module`);
  });
}

function updateOutput(rangeSelector, outputSelector) {
  const range = qs(rangeSelector);
  const output = qs(outputSelector);
  output.value = range.value;
  range.addEventListener("input", () => {
    output.value = range.value;
  });
}

function setPreview(input, image) {
  const preview = image.closest(".preview");
  const file = input.files && input.files[0];
  updateFileName(input);
  if (fileObjectUrls.has(image)) {
    URL.revokeObjectURL(fileObjectUrls.get(image));
    fileObjectUrls.delete(image);
  }
  if (!file) {
    image.removeAttribute("src");
    preview.classList.remove("has-image");
    return;
  }
  const url = URL.createObjectURL(file);
  fileObjectUrls.set(image, url);
  image.src = url;
  preview.classList.add("has-image");
}

function updateFileName(input) {
  const picker = input.closest(".file-picker");
  if (!picker) return;
  const nameTarget = picker.querySelector("em");
  if (!nameTarget) return;
  const files = [...(input.files || [])];
  if (!files.length) {
    nameTarget.textContent = nameTarget.dataset.empty || "未选择文件";
    return;
  }
  nameTarget.textContent = files.length === 1 ? files[0].name : `${files.length} 个文件`;
}

function initFilePickers() {
  qsa(".file-picker").forEach((picker) => {
    const input = picker.querySelector('input[type="file"]');
    if (!input) return;

    input.addEventListener("change", () => updateFileName(input));

    picker.addEventListener("click", (event) => {
      if (event.target !== input) {
        input.focus();
      }
    });

    picker.addEventListener("dragover", (event) => {
      event.preventDefault();
      picker.classList.add("dragging");
    });

    picker.addEventListener("dragleave", () => {
      picker.classList.remove("dragging");
    });

    picker.addEventListener("drop", (event) => {
      event.preventDefault();
      picker.classList.remove("dragging");
      if (!event.dataTransfer || !event.dataTransfer.files.length) return;
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

function setResult(target, html, isError = false) {
  target.hidden = false;
  target.classList.toggle("error", isError);
  target.setAttribute("role", isError ? "alert" : "status");
  target.innerHTML = html;
}

function clearResult(target) {
  target.hidden = true;
  target.classList.remove("error");
  target.innerHTML = "";
}

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null || Number.isNaN(Number(bytes))) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function formatSeconds(seconds) {
  return `${Number(seconds).toFixed(3)} 秒`;
}

function imageStats(stats) {
  const parts = [
    `尺寸 ${stats.width}×${stats.height}`,
    `通道 ${stats.channels}`,
    `LSB ${stats.numLsb}`,
  ];
  if (stats.capacityBytes !== undefined) parts.push(`容量 ${formatBytes(stats.capacityBytes)}`);
  if (stats.payloadBytes !== undefined) parts.push(`载荷 ${formatBytes(stats.payloadBytes || 0)}`);
  if (stats.storedBytes !== undefined && stats.storedBytes !== stats.payloadBytes) parts.push(`写入 ${formatBytes(stats.storedBytes)}`);
  if (stats.outputBytes !== undefined) parts.push(`输出 ${formatBytes(stats.outputBytes)}`);
  return parts.join(" ｜ ");
}

function wavStats(stats) {
  return [
    `声道 ${stats.channels}`,
    `采样率 ${stats.frameRate} Hz`,
    `时长 ${formatSeconds(stats.durationSeconds || 0)}`,
    `LSB ${stats.numLsb}`,
    `容量 ${formatBytes(stats.capacityBytes)}`,
    `载荷 ${formatBytes(stats.payloadBytes || 0)}`,
    stats.storedBytes !== undefined && stats.storedBytes !== stats.payloadBytes ? `写入 ${formatBytes(stats.storedBytes)}` : "",
  ].filter(Boolean).join(" ｜ ");
}

function downloadLink(dataUrl, filename, label) {
  return `<a class="download-link" href="${dataUrl}" download="${filename}">${label}</a>`;
}

function binaryDownloadLink(base64, filename, label, mimeType = "application/octet-stream") {
  return downloadLink(`data:${mimeType};base64,${base64}`, filename, label);
}

async function postForm(url, form) {
  const response = await fetch(url, {
    method: "POST",
    body: new FormData(form),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "操作失败");
  }
  return payload;
}

async function withBusy(button, busyText, action) {
  const normalText = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try {
    return await action();
  } finally {
    button.disabled = false;
    button.textContent = normalText;
  }
}

function initPayloadMode(form) {
  const box = form.querySelector(".payload-box");
  form.querySelectorAll('input[name="payload_mode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      box.classList.toggle("file-mode", radio.value === "file" && radio.checked);
    });
  });
}

qsa(".module-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchModule(tab.dataset.module));
});

themeButton.addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
});

updateOutput("#imageLsb", "#imageLsbValue");
updateOutput("#compression", "#compressionValue");
updateOutput("#imageExtractLsb", "#imageExtractLsbValue");
updateOutput("#wavLsb", "#wavLsbValue");
updateOutput("#wavExtractLsb", "#wavExtractLsbValue");
updateOutput("#detectLsb", "#detectLsbValue");

initPayloadMode(qs("#imageEmbedForm"));
initPayloadMode(qs("#wavEmbedForm"));
initFilePickers();

qs("#carrierInput").addEventListener("change", () => setPreview(qs("#carrierInput"), qs("#carrierPreview")));
qs("#stegoInput").addEventListener("change", () => setPreview(qs("#stegoInput"), qs("#stegoPreview")));
qs("#detectInput").addEventListener("change", () => setPreview(qs("#detectInput"), qs("#detectPreview")));

qs("#imageAnalyzeBtn").addEventListener("click", async () => {
  const form = qs("#imageEmbedForm");
  const result = qs("#imageEmbedResult");
  clearResult(result);
  await withBusy(qs("#imageAnalyzeBtn"), "分析中...", async () => {
    try {
      const payload = await postForm("/api/image/analyze", form);
      const fitText = payload.stats.fits ? "容量充足" : "容量不足";
      setResult(result, `${fitText}：${imageStats(payload.stats)}`, !payload.stats.fits);
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

qs("#imageEmbedForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = qs("#imageEmbedResult");
  clearResult(result);
  await withBusy(event.submitter, "生成中...", async () => {
    try {
      const payload = await postForm("/api/image/embed", event.currentTarget);
      qs("#encodedPreview").src = payload.image;
      qs("#encodedPreview").closest(".preview").classList.add("has-image");
      setResult(
        result,
        `图片已生成：${imageStats(payload.stats)} ${downloadLink(payload.image, payload.filename, "下载图片")}`
      );
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

qs("#imageExtractForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = qs("#imageExtractResult");
  clearResult(result);
  qs("#imageRecoveredText").value = "";
  await withBusy(event.submitter, "提取中...", async () => {
    try {
      const payload = await postForm("/api/image/extract", event.currentTarget);
      qs("#imageRecoveredText").value = payload.text;
      const textState = payload.stats.isUtf8Text ? "文本已识别" : "包含非文本字节";
      const payloadName = payload.stats.legacyPayload ? "兼容载荷" : `文件 ${payload.downloadName}`;
      setResult(
        result,
        `${textState}｜${payloadName}：${imageStats(payload.stats)} ${binaryDownloadLink(payload.dataBase64, payload.downloadName, "下载载荷", payload.mimeType)}`
      );
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

qs("#wavAnalyzeBtn").addEventListener("click", async () => {
  const form = qs("#wavEmbedForm");
  const result = qs("#wavEmbedResult");
  clearResult(result);
  await withBusy(qs("#wavAnalyzeBtn"), "分析中...", async () => {
    try {
      const payload = await postForm("/api/wav/analyze", form);
      const fitText = payload.stats.fits ? "容量充足" : "容量不足";
      setResult(result, `${fitText}：${wavStats(payload.stats)}`, !payload.stats.fits);
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

qs("#wavEmbedForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = qs("#wavEmbedResult");
  clearResult(result);
  await withBusy(event.submitter, "生成中...", async () => {
    try {
      const payload = await postForm("/api/wav/embed", event.currentTarget);
      qs("#encodedAudio").src = payload.audio;
      setResult(
        result,
        `音频已生成：${wavStats(payload.stats)} ${downloadLink(payload.audio, payload.filename, "下载音频")}`
      );
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

qs("#wavExtractForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = qs("#wavExtractResult");
  clearResult(result);
  qs("#wavRecoveredText").value = "";
  await withBusy(event.submitter, "提取中...", async () => {
    try {
      const payload = await postForm("/api/wav/extract", event.currentTarget);
      qs("#wavRecoveredText").value = payload.text;
      const lengthMode = payload.stats.usedAutoLength ? "自动长度" : "手动长度";
      const payloadName = payload.stats.legacyPayload ? "兼容载荷" : `文件 ${payload.downloadName}`;
      setResult(
        result,
        `${lengthMode}｜${payloadName}：${wavStats(payload.stats)} ${binaryDownloadLink(payload.dataBase64, payload.downloadName, "下载载荷", payload.mimeType)}`
      );
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

qs("#detectForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = qs("#detectResult");
  clearResult(result);
  await withBusy(event.submitter, "生成中...", async () => {
    try {
      const payload = await postForm("/api/image/detect", event.currentTarget);
      qs("#detectOutputPreview").src = payload.image;
      qs("#detectOutputPreview").closest(".preview").classList.add("has-image");
      setResult(
        result,
        `可视化图已生成：${imageStats(payload.stats)} ${downloadLink(payload.image, payload.filename, "下载图片")}`
      );
    } catch (error) {
      setResult(result, error.message, true);
    }
  });
});

fetch("/health")
  .then((response) => response.json())
  .then((payload) => {
    qs("#serverStatus").textContent = payload.ok ? "服务正常" : "服务异常";
    qs("#serverStatus").classList.toggle("is-error", !payload.ok);
  })
  .catch(() => {
    qs("#serverStatus").textContent = "服务未连接";
    qs("#serverStatus").classList.add("is-error");
  });

setTheme(localStorage.getItem("stego-webui-theme") || "dark");

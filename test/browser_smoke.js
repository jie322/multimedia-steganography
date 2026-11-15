const fs = require("node:fs");
const path = require("node:path");

async function runBrowserSmoke(page) {
  const base = path.resolve(__dirname, "..");
  const output = base + "/output/playwright/";
  fs.mkdirSync(output, {recursive: true});
  fs.mkdirSync(path.join(base, "output", "testing"), {recursive: true});
  const text = "\u6bd5\u4e1a\u8bbe\u8ba1\u9690\u5199\u6d4b\u8bd5\uff1a\u4e2d\u6587\u3001English\u3001\u65e5\u672c\u8a9e\u3002\nLine 2: 0123456789 <tag> & symbols.\n";
  const results = [];
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  async function check(name, action) {
    try {
      const details = await action();
      results.push({name, status: "PASS", details});
    } catch (error) {
      results.push({name, status: "FAIL", error: String(error)});
    }
  }
  function requireValue(condition, message) {
    if (!condition) throw new Error(message);
  }
  async function download(selector, filename) {
    const [file] = await Promise.all([
      page.waitForEvent("download"), page.locator(selector).click()
    ]);
    const destination = base + "/output/testing/" + filename;
    await file.saveAs(destination);
    return destination;
  }
  async function submit(form, result) {
    await page.locator(form + ' button[type="submit"]').click();
    await page.waitForFunction(selector => {
      const element = document.querySelector(selector);
      return !element.hidden && (element.classList.contains("error") || element.querySelector("a[download]"));
    }, result);
    requireValue(!await page.locator(result).evaluate(el => el.classList.contains("error")), await page.locator(result).innerText());
  }
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto(process.env.STEGO_BASE_URL || "http://127.0.0.1:9001/");
  await page.waitForFunction(() => document.querySelector("#serverStatus").textContent === "\u670d\u52a1\u6b63\u5e38");

  await check("Browser JPG text embed/download/extract", async () => {
    await page.locator("#carrierInput").setInputFiles(base + "/test_resources/downloads/test.jpg");
    await page.locator("#imageSecretText").fill(text);
    await page.locator("#imageAnalyzeBtn").click();
    await page.waitForFunction(() => document.querySelector("#imageEmbedResult").textContent.includes("\u5bb9\u91cf\u5145\u8db3"));
    await submit("#imageEmbedForm", "#imageEmbedResult");
    const image = await download("#imageEmbedResult a[download]", "browser_image_text.png");
    await page.locator("#stegoInput").setInputFiles(image);
    await submit("#imageExtractForm", "#imageExtractResult");
    requireValue(await page.locator("#imageRecoveredText").inputValue() === text, "Unicode text differs");
    requireValue(await page.locator("#encodedPreview").evaluate(el => el.complete && el.naturalWidth === 2133), "Image preview did not render");
    await page.screenshot({path: output + "image-desktop.png", fullPage: true});
  });
  await check("Browser JPG hides and downloads restored MP4", async () => {
    await page.locator('#imageEmbedForm input[value="file"]').check();
    await page.locator("#imageSecretFile").setInputFiles(base + "/test_resources/payloads/payload_video_5s.mp4");
    await submit("#imageEmbedForm", "#imageEmbedResult");
    const image = await download("#imageEmbedResult a[download]", "browser_image_video.png");
    await page.locator("#stegoInput").setInputFiles(image);
    await submit("#imageExtractForm", "#imageExtractResult");
    requireValue(await page.locator("#imageExtractResult a").getAttribute("download") === "payload_video_5s.mp4", "Original video filename lost");
    return {recoveredFile: await download("#imageExtractResult a[download]", "browser_recovered_video.mp4")};
  });
  await check("Browser drag-and-drop image upload", async () => {
    await page.locator("#carrierInput").evaluate(input => {
      const transfer = new DataTransfer();
      transfer.items.add(input.files[0]);
      input.closest(".file-picker").dispatchEvent(new DragEvent("drop", {bubbles: true, cancelable: true, dataTransfer: transfer}));
    });
    requireValue(await page.locator("#carrierInput").evaluate(el => el.files[0].name === "test.jpg"), "Dropped image not selected");
  });
  await page.locator('[data-module="wav"]').click();
  await check("Browser WAV text embed/download/automatic extract/playback", async () => {
    await page.locator("#wavCarrierInput").setInputFiles(base + "/test_resources/carriers/audio_20s.wav");
    await page.locator("#wavSecretText").fill(text);
    await page.locator("#wavAnalyzeBtn").click();
    await page.waitForFunction(() => document.querySelector("#wavEmbedResult").textContent.includes("\u5bb9\u91cf\u5145\u8db3"));
    await submit("#wavEmbedForm", "#wavEmbedResult");
    const wav = await download("#wavEmbedResult a[download]", "browser_audio_text.wav");
    await page.locator("#wavStegoInput").setInputFiles(wav);
    await submit("#wavExtractForm", "#wavExtractResult");
    requireValue(await page.locator("#wavRecoveredText").inputValue() === text, "WAV Unicode text differs");
    await page.waitForFunction(() => document.querySelector("#encodedAudio").readyState >= 1);
    const audio = await page.locator("#encodedAudio").evaluate(async el => {
      el.muted = true;
      await el.play();
      const state = {duration: el.duration, paused: el.paused, error: el.error};
      el.pause();
      return state;
    });
    requireValue(audio.duration === 20 && !audio.paused && !audio.error, "WAV playback failed");
    await page.screenshot({path: output + "audio-desktop.png", fullPage: true});
    return audio;
  });
  await check("Browser WAV hides JPG and downloads restored image", async () => {
    await page.locator('#wavEmbedForm input[value="file"]').check();
    await page.locator("#wavSecretFile").setInputFiles(base + "/test_resources/payloads/payload_image.jpg");
    await submit("#wavEmbedForm", "#wavEmbedResult");
    const wav = await download("#wavEmbedResult a[download]", "browser_audio_image.wav");
    await page.locator("#wavStegoInput").setInputFiles(wav);
    await submit("#wavExtractForm", "#wavExtractResult");
    requireValue(await page.locator("#wavExtractResult a").getAttribute("download") === "payload_image.jpg", "JPG filename lost");
    return {recoveredFile: await download("#wavExtractResult a[download]", "browser_recovered_image.jpg")};
  });
  await page.locator('[data-module="detect"]').click();
  await check("Browser LSB visualization", async () => {
    await page.locator("#detectInput").setInputFiles(base + "/test_resources/downloads/test.jpg");
    await submit("#detectForm", "#detectResult");
    await page.waitForFunction(() => document.querySelector("#detectOutputPreview").naturalWidth === 2133);
    await download("#detectResult a[download]", "browser_lsb_view.png");
    await page.screenshot({path: output + "detect-desktop.png", fullPage: true});
  });
  await check("Mobile image/audio/detection layout", async () => {
    await page.setViewportSize({width: 390, height: 844});
    const layouts = [];
    for (const module of ["image", "wav", "detect"]) {
      await page.locator(`[data-module="${module}"]`).click();
      const dimensions = await page.evaluate(() => ({viewport: innerWidth, document: document.documentElement.scrollWidth}));
      await page.screenshot({path: output + module + "-mobile.png", fullPage: true});
      layouts.push({module, ...dimensions});
      requireValue(dimensions.document <= dimensions.viewport, "Horizontal overflow: " + JSON.stringify(layouts));
    }
    return layouts;
  });
  await check("Theme toggle persists after reload", async () => {
    const before = await page.locator("html").getAttribute("data-theme");
    await page.locator("#themeButton").click();
    const after = await page.locator("html").getAttribute("data-theme");
    requireValue(after !== before, "Theme did not change");
    await page.reload();
    requireValue(await page.locator("html").getAttribute("data-theme") === after, "Theme was not persisted");
    await page.screenshot({path: output + "theme-mobile.png", fullPage: true});
  });
  await check("Browser empty secret shows error and restores button", async () => {
    await page.locator("#carrierInput").setInputFiles(base + "/test_resources/downloads/test.jpg");
    await page.locator('#imageEmbedForm button[type="submit"]').click();
    await page.waitForFunction(() => !document.querySelector("#imageEmbedResult").hidden);
    requireValue(await page.locator("#imageEmbedResult").evaluate(el => el.classList.contains("error")), "Missing input was not rejected");
    requireValue(!await page.locator('#imageEmbedForm button[type="submit"]').isDisabled(), "Button stuck disabled");
  });
  await check("No uncaught browser JavaScript errors", async () => {
    requireValue(errors.length === 0, JSON.stringify(errors));
  });
  await page.setViewportSize({width:1440,height:1000});
  return {results, errors, summary: {total: results.length, passed: results.filter(r => r.status === "PASS").length, failed: results.filter(r => r.status === "FAIL").length}};
}

module.exports = runBrowserSmoke;

if (require.main === module) {
  (async () => {
    const {chromium} = require("playwright");
    const browser = await chromium.launch({headless: true});
    try {
      const page = await browser.newPage();
      const report = await runBrowserSmoke(page);
      fs.writeFileSync(path.join(__dirname, "..", "output", "testing", "browser_results.json"), JSON.stringify(report, null, 2));
      console.log(JSON.stringify(report.summary));
      if (report.summary.failed) process.exitCode = 1;
    } finally {
      await browser.close();
    }
  })().catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}

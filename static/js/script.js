// 給与欄の表示切替、重要操作の確認、一回限りの発行結果のコピーを補助します。
"use strict";

document.querySelectorAll("[data-pay-form]").forEach((form) => {
    const payType = form.querySelector("[data-pay-type]");
    const employmentType = form.querySelector("[data-employment-type]");
    const hourlyField = form.querySelector("[data-hourly-field]");
    const hourlyInput = form.querySelector("[data-hourly-input]");
    let manualSelection = Boolean(payType.value);

    const updateHourlyField = () => {
        const hourly = payType.value === "hourly";
        hourlyField.hidden = !hourly;
        hourlyInput.disabled = !hourly;
    };
    const setDefaultPayType = () => {
        if (!employmentType || manualSelection) return;
        const type = employmentType.value;
        payType.value = type === "E" ? "monthly" : ["P", "A"].includes(type) ? "hourly" : "";
        updateHourlyField();
    };
    payType.addEventListener("change", () => {
        manualSelection = Boolean(payType.value);
        if (!manualSelection) setDefaultPayType();
        updateHourlyField();
    });
    if (employmentType) employmentType.addEventListener("change", setDefaultPayType);
    setDefaultPayType();
    updateHourlyField();
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
        if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
        const target = document.getElementById(button.dataset.copyTarget);
        const status = document.querySelector("[data-copy-status]");
        if (!target || !status) return;
        try {
            await navigator.clipboard.writeText(target.textContent.trim());
            status.textContent = "コピーしました。本人以外には共有せず、伝達後はクリップボードも消去してください。";
        } catch {
            const range = document.createRange();
            range.selectNodeContents(target);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            status.textContent = "自動コピーできませんでした。選択した文字列をCtrl+Cでコピーしてください。";
        }
    });
});

// 戻る操作のBFCacheからも秘密値の表示を復元させない（HTTPもno-store）。
if (document.getElementById("issued-temporary-password")) {
    window.addEventListener("pagehide", () => {
        document.getElementById("issued-temporary-password").textContent = "表示終了";
    });
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) window.location.replace("/admin/employees");
    });
}

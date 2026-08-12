const commandData = {
  join: {
    category: "VOICE CONTROL",
    command: "/입장",
    title: "지금 있는 음성 채널에서 TTS를 시작합니다.",
    description:
      "별도 채널 설정 없이 현재 음성 채널과 연결된 채팅을 바로 읽기 시작합니다.",
    result: "음성 채널에 입장했어요. 이제 채팅을 읽어드릴게요.",
  },
  rank: {
    category: "WEEKLY ACTIVITY",
    command: "/활동순위",
    title: "이번 주 서버 활동 TOP 10을 확인합니다.",
    description:
      "메시지와 음성 채널 활동을 서버 안에서 집계해 현재 순위를 보여줍니다.",
    result: "이번 주 활동 순위를 불러왔어요. 서버 멤버의 기록만 표시합니다.",
  },
  party: {
    category: "PARTY MATCH",
    command: "/파티모집",
    title: "참가와 대기까지 버튼으로 정리합니다.",
    description:
      "게임, 정원, 시작 시간을 정하면 참가자가 직접 상태를 바꿀 수 있는 모집글을 만듭니다.",
    result: "파티 모집을 시작했어요. 정원이 차면 대기열로 안내합니다.",
  },
  fortune: {
    category: "DAILY PLAY",
    command: "/오늘의운세",
    title: "오늘의 게임운과 관계운을 확인합니다.",
    description:
      "사용자 ID와 KST 날짜로 계산해 같은 날에는 같은 결과를 보여주는 오락용 기능입니다.",
    result: "오늘의 운세를 만들었어요. 원한다면 서버에 공유할 수 있어요.",
  },
  record: {
    category: "GAME RECORD",
    command: "/롤 전적",
    title: "등록한 라이엇 ID의 최근 기록을 찾습니다.",
    description:
      "현재 랭크와 최근 경기 결과를 Discord를 벗어나지 않고 확인합니다.",
    result: "등록된 계정의 랭크와 최근 경기 기록을 불러왔어요.",
  },
};

const commandOutput = document.querySelector(".command-output");
const commandTabs = [...document.querySelectorAll(".command-tab")];
const outputCategory = document.querySelector("#output-category");
const outputCommand = document.querySelector("#output-command");
const outputTitle = document.querySelector("#output-title");
const outputDescription = document.querySelector("#output-description");
const outputResult = document.querySelector("#output-result");
const copyButton = document.querySelector(".copy-command");

function selectCommand(key) {
  const item = commandData[key];
  if (!item) return;

  commandTabs.forEach((tab) => {
    const isActive = tab.dataset.command === key;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.tabIndex = isActive ? 0 : -1;
  });

  commandOutput.classList.remove("is-switching");
  void commandOutput.offsetWidth;

  outputCategory.textContent = item.category;
  outputCommand.textContent = item.command;
  outputTitle.textContent = item.title;
  outputDescription.textContent = item.description;
  outputResult.textContent = item.result;
  copyButton.dataset.copyCommand = item.command;
  copyButton.textContent = "명령 복사";
  commandOutput.setAttribute("aria-labelledby", `tab-${key}`);
  commandOutput.classList.add("is-switching");
}

commandTabs.forEach((tab) => {
  tab.addEventListener("click", () => selectCommand(tab.dataset.command));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;

    event.preventDefault();
    const currentIndex = commandTabs.indexOf(tab);
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const nextIndex = (currentIndex + direction + commandTabs.length) % commandTabs.length;
    const nextTab = commandTabs[nextIndex];
    selectCommand(nextTab.dataset.command);
    nextTab.focus();
  });
});

copyButton?.addEventListener("click", async () => {
  const command = copyButton.dataset.copyCommand;
  try {
    await navigator.clipboard.writeText(command);
    copyButton.textContent = "복사됨";
  } catch {
    copyButton.textContent = command;
  }

  window.setTimeout(() => {
    copyButton.textContent = "명령 복사";
  }, 1600);
});

const root = document.documentElement;
const themeButton = document.querySelector(".theme-toggle");
const themeLabel = document.querySelector(".theme-label");
const themeOrder = ["auto", "light", "dark"];

function applyTheme(theme) {
  if (theme === "auto") {
    root.removeAttribute("data-theme");
  } else {
    root.dataset.theme = theme;
  }

  themeLabel.textContent = theme.toUpperCase();
  themeButton.setAttribute("aria-label", `색상 모드 변경, 현재 ${theme}`);
  localStorage.setItem("koa-bot-theme", theme);
}

const savedTheme = localStorage.getItem("koa-bot-theme");
if (themeOrder.includes(savedTheme)) applyTheme(savedTheme);

themeButton?.addEventListener("click", () => {
  const current = root.dataset.theme || "auto";
  const nextIndex = (themeOrder.indexOf(current) + 1) % themeOrder.length;
  applyTheme(themeOrder[nextIndex]);
});

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const revealItems = document.querySelectorAll(".reveal");

if (reduceMotion || !("IntersectionObserver" in window)) {
  revealItems.forEach((item) => item.classList.add("is-visible"));
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.16 },
  );

  revealItems.forEach((item) => observer.observe(item));
}

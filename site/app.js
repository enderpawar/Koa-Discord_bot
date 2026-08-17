(() => {
  const body = document.body;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const loader = document.querySelector(".page-loader");
  const loaderBar = document.querySelector(".page-loader > b");
  const menuButton = document.querySelector(".mobile-menu-button");
  const mobilePanel = document.querySelector(".mobile-panel");
  const searchInput = document.querySelector("#command-search");
  const commandRows = [...document.querySelectorAll(".command-row")];
  const commandGroups = [...document.querySelectorAll(".command-groups > details")];
  const commandCount = document.querySelector("#command-count");
  const emptySearch = document.querySelector(".empty-search");
  const sectionLinks = [...document.querySelectorAll('.side-nav a[href^="#"], .page-toc a[href^="#"]')];
  const brandTrigger = document.querySelector(".toc-brand-trigger");
  const brandLightbox = document.querySelector("#brand-lightbox");
  const brandLightboxClose = document.querySelector(".image-lightbox-close");
  const cleanup = [];

  const revealPage = () => {
    body.classList.remove("is-loading");
    if (loader) loader.hidden = true;
  };

  const safetyTimer = window.setTimeout(revealPage, 3200);

  const runIntro = () => {
    window.clearTimeout(safetyTimer);

    if (!loader || reduceMotion || !window.gsap) {
      revealPage();
      return;
    }

    window.gsap.timeline({ defaults: { ease: "power4.out" } })
      .to(loaderBar, { scaleX: 1, duration: 0.78 })
      .to(loader, { yPercent: -100, duration: 0.9, delay: 0.18, onComplete: revealPage });
  };

  const setMenuOpen = (open) => {
    if (!menuButton || !mobilePanel) return;
    menuButton.setAttribute("aria-expanded", String(open));
    menuButton.setAttribute("aria-label", open ? "메뉴 닫기" : "메뉴 열기");
    menuButton.querySelector("i")?.classList.toggle("ph-x", open);
    menuButton.querySelector("i")?.classList.toggle("ph-list", !open);
    mobilePanel.hidden = !open;
  };

  if (menuButton && mobilePanel) {
    const onMenuClick = () => setMenuOpen(mobilePanel.hidden);
    const onMenuLinkClick = () => setMenuOpen(false);
    const onEscape = (event) => {
      if (event.key === "Escape") setMenuOpen(false);
    };

    menuButton.addEventListener("click", onMenuClick);
    mobilePanel.querySelectorAll("a").forEach((link) => link.addEventListener("click", onMenuLinkClick));
    document.addEventListener("keydown", onEscape);

    cleanup.push(() => menuButton.removeEventListener("click", onMenuClick));
    cleanup.push(() => mobilePanel.querySelectorAll("a").forEach((link) => link.removeEventListener("click", onMenuLinkClick)));
    cleanup.push(() => document.removeEventListener("keydown", onEscape));
  }

  const normalize = (value) => value.toLocaleLowerCase("ko-KR").replace(/\s+/g, " ").trim();

  const filterCommands = () => {
    const query = normalize(searchInput?.value || "");
    let visibleCount = 0;

    commandRows.forEach((row) => {
      const haystack = normalize(`${row.dataset.search || ""} ${row.textContent || ""}`);
      const visible = !query || haystack.includes(query);
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });

    commandGroups.forEach((group) => {
      const hasVisibleRow = [...group.querySelectorAll(".command-row")].some((row) => !row.hidden);
      group.hidden = !hasVisibleRow;
      if (query && hasVisibleRow) group.open = true;
    });

    if (commandCount) commandCount.textContent = `${visibleCount}개 명령어`;
    if (emptySearch) emptySearch.hidden = visibleCount !== 0;
  };

  if (searchInput) {
    const onSearch = () => filterCommands();
    const onSearchKeyDown = (event) => {
      if (event.key === "Escape") {
        searchInput.value = "";
        filterCommands();
        searchInput.blur();
      }
    };
    const onShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        searchInput.focus();
        searchInput.select();
      }
    };

    searchInput.addEventListener("input", onSearch);
    searchInput.addEventListener("keydown", onSearchKeyDown);
    document.addEventListener("keydown", onShortcut);

    cleanup.push(() => searchInput.removeEventListener("input", onSearch));
    cleanup.push(() => searchInput.removeEventListener("keydown", onSearchKeyDown));
    cleanup.push(() => document.removeEventListener("keydown", onShortcut));
  }

  if (brandTrigger && brandLightbox && brandLightboxClose) {
    const openBrandLightbox = () => {
      body.classList.add("is-lightbox-open");
      brandLightbox.showModal();
    };
    const closeBrandLightbox = () => brandLightbox.close();
    const closeBrandLightboxFromBackdrop = (event) => {
      if (event.target === brandLightbox) closeBrandLightbox();
    };
    const restoreAfterBrandLightbox = () => {
      body.classList.remove("is-lightbox-open");
      brandTrigger.focus({ preventScroll: true });
    };

    brandTrigger.addEventListener("click", openBrandLightbox);
    brandLightboxClose.addEventListener("click", closeBrandLightbox);
    brandLightbox.addEventListener("click", closeBrandLightboxFromBackdrop);
    brandLightbox.addEventListener("close", restoreAfterBrandLightbox);

    cleanup.push(() => brandTrigger.removeEventListener("click", openBrandLightbox));
    cleanup.push(() => brandLightboxClose.removeEventListener("click", closeBrandLightbox));
    cleanup.push(() => brandLightbox.removeEventListener("click", closeBrandLightboxFromBackdrop));
    cleanup.push(() => brandLightbox.removeEventListener("close", restoreAfterBrandLightbox));
  }

  const sections = sectionLinks
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter((section, index, items) => section && items.indexOf(section) === index);

  if ("IntersectionObserver" in window && sections.length) {
    const setActiveSection = (id) => {
      sectionLinks.forEach((link) => {
        const active = link.getAttribute("href") === `#${id}`;
        link.classList.toggle("is-active", active);
        if (active) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      });
    };

    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) setActiveSection(visible.target.id);
    }, {
      rootMargin: "-18% 0px -62% 0px",
      threshold: [0, 0.2, 0.5],
    });

    sections.forEach((section) => observer.observe(section));
    cleanup.push(() => observer.disconnect());
  }

  if (document.readyState === "complete") runIntro();
  else window.addEventListener("load", runIntro, { once: true });

  window.addEventListener("pagehide", () => {
    cleanup.forEach((dispose) => dispose());
  }, { once: true });
})();

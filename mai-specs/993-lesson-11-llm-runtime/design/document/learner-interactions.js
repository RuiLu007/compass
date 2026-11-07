(() => {
  const dialog = document.getElementById("screenshot-dialog");
  const full = document.getElementById("screenshot-full");
  document.querySelectorAll("[data-screenshot]").forEach((button) => {
    button.addEventListener("click", () => {
      const source = button.querySelector("img");
      full.src = source.src;
      full.alt = source.alt;
      dialog.showModal();
      dialog.querySelector(".screenshot-viewport").scrollTo(0, 0);
    });
  });
})();

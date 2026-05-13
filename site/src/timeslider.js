import { dateRange, toISODate } from "./util.js";
import { setState, getState } from "./state.js";

export function initTimeSlider(onChange) {
  const slider = document.getElementById("timeslider");
  const label = document.getElementById("timelabel");
  const { min, max, step } = dateRange();
  slider.min = String(min);
  slider.max = String(max);
  slider.step = String(step);

  const initial = getState().date ? Date.parse(getState().date) : Date.now();
  slider.value = String(initial);
  label.textContent = labelFor(initial);

  let debounce;
  slider.addEventListener("input", () => {
    const v = Number(slider.value);
    label.textContent = labelFor(v);
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      setState({ date: toISODate(v) }, true);
      onChange(v);
    }, 60);
  });

  return {
    setDate(ms) {
      slider.value = String(ms);
      label.textContent = labelFor(ms);
    },
    current() {
      return Number(slider.value);
    },
  };
}

function labelFor(ms) {
  const today = new Date().setHours(0, 0, 0, 0);
  if (ms >= today && ms < today + 86_400_000) return "vandaag";
  return toISODate(ms);
}

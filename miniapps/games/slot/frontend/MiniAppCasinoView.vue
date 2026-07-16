<template>
  <section class="miniapp-shell">
    <div
      class="miniapp-window"
      :class="{ 'is-window-moving': windowInteractionMode !== 'idle' }"
      :style="miniAppWindowStyle"
      role="dialog"
      aria-label="Prime Casino mini app"
    >
      <div class="miniapp-window-bar" @pointerdown="startWindowDrag">
        <div class="miniapp-window-brand">
          <span class="miniapp-window-icon" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
            <span></span>
          </span>
          <div>
            <span>Mini App</span>
            <strong>Prime Casino</strong>
          </div>
        </div>
      </div>

      <div class="miniapp-window-body">
        <div v-if="errorMessage" class="miniapp-alert miniapp-alert-danger">{{ errorMessage }}</div>
        <div v-if="spinMessage" class="miniapp-alert" :class="spinMessageTone === 'win' ? 'miniapp-alert-success' : 'miniapp-alert-info'">
          {{ spinMessage }}
        </div>

        <div class="miniapp-machine-wrap">
          <div class="slot-machine" :class="{ 'is-spinning': isSpinning }">
            <div class="slot-marquee">
              <span v-for="lamp in 14" :key="lamp" class="slot-lamp"></span>
            </div>

            <div class="slot-top">
              <div class="slot-brand">PRIME JACKPOT</div>
              <div class="slot-display">
                <span>{{ isSpinning ? 'SPINNING' : 'READY' }}</span>
                <strong>{{ formatUsdtValue(bet) }} USDT</strong>
              </div>
            </div>

            <div class="slot-window-frame">
              <div class="slot-window">
                <div
                  v-for="reel in reelOrder"
                  :key="reel"
                  class="slot-reel"
                  :class="{ 'is-spinning': spinningReels[reel] }"
                >
                  <div class="slot-symbol" :class="`slot-symbol-${reelSymbols[reel]}`">
                    <template v-if="reelSymbols[reel] === 'seven'">
                      <span class="symbol-seven">7</span>
                    </template>
                    <template v-else-if="reelSymbols[reel] === 'bar'">
                      <span class="symbol-bar">BAR</span>
                    </template>
                    <template v-else-if="reelSymbols[reel] === 'diamond'">
                      <span class="symbol-diamond"></span>
                    </template>
                    <template v-else-if="reelSymbols[reel] === 'star'">
                      <span class="symbol-star">★</span>
                    </template>
                    <template v-else-if="reelSymbols[reel] === 'cherry'">
                      <span class="symbol-cherry">
                        <i></i>
                        <i></i>
                      </span>
                    </template>
                    <template v-else>
                      <span class="symbol-lemon"></span>
                    </template>
                  </div>
                </div>
              </div>
              <div class="slot-window-gloss"></div>
              <div class="slot-lever-groove" aria-hidden="true"></div>

              <button
                type="button"
                class="slot-lever"
                :class="[`slot-lever-phase-${leverPhase}`, { 'is-disabled': isSpinning }]"
                :disabled="isSpinning"
                @click="spin"
                aria-label="Spin the slot machine"
              >
                <span class="slot-lever-stem"></span>
                <span class="slot-lever-knob"></span>
              </button>
            </div>

            <div class="slot-base"></div>
          </div>

          <div class="miniapp-controls">
            <div class="miniapp-bet-controls">
              <div class="miniapp-step-side miniapp-step-side-left" aria-label="Select bet step">
                <button
                  v-for="step in betStepOptions"
                  :key="`left-step-${step}`"
                  type="button"
                  class="miniapp-step-choice"
                  :class="{ 'is-active': activeStep === step }"
                  :disabled="isSpinning"
                  @click="selectBetStep(step)"
                >
                  {{ formatStepValue(step) }}
                </button>
              </div>
              <button
                type="button"
                class="miniapp-stepper miniapp-stepper-minus"
                :disabled="isSpinning || bet <= config.minBet"
                @click="changeBet(-1)"
              >
                -
              </button>
              <div class="miniapp-bet-box">
                <div class="miniapp-bet-label">Current Bet</div>
                <div class="miniapp-bet-value">{{ formatUsdtValue(bet) }}</div>
                <div class="miniapp-bet-step-copy">Step now: {{ formatStepValue(activeStep) }} USDT</div>
              </div>
              <button
                type="button"
                class="miniapp-stepper miniapp-stepper-plus"
                :disabled="isSpinning || bet >= config.maxBet"
                @click="changeBet(1)"
              >
                +
              </button>
              <div class="miniapp-step-side miniapp-step-side-right" aria-label="Select bet step">
                <button
                  v-for="step in betStepOptions"
                  :key="`right-step-${step}`"
                  type="button"
                  class="miniapp-step-choice"
                  :class="{ 'is-active': activeStep === step }"
                  :disabled="isSpinning"
                  @click="selectBetStep(step)"
                >
                  {{ formatStepValue(step) }}
                </button>
              </div>
            </div>

            <button type="button" class="miniapp-spin" :disabled="isSpinning" @click="spin">
              {{ isSpinning ? 'Spinning...' : 'Pull Lever' }}
            </button>
          </div>
        </div>
      </div>

      <button
        type="button"
        class="miniapp-resize-handle"
        aria-label="Изменить размер окна"
        @pointerdown.stop.prevent="startWindowResize"
      ></button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import axios from 'axios';
import api from '@/api/client';

type MiniAppCasinoConfig = {
  rtp: number;
  minBet: number;
  maxBet: number;
  betStepMin: number;
  betStepMax: number;
};

type MiniAppCasinoResponse = {
  ok: boolean;
  game: string;
  emoji: string;
  config: MiniAppCasinoConfig;
};

type ReelId = 'left' | 'center' | 'right';
type SymbolId = 'seven' | 'bar' | 'diamond' | 'star' | 'cherry' | 'lemon';
type WindowInteractionMode = 'idle' | 'drag' | 'resize';

type MiniAppCasinoSpinResponse = {
  ok: boolean;
  game: string;
  symbols: Record<ReelId, string>;
  isWin: boolean;
  multiplier: number;
  betAmount: number;
  payoutAmount: number;
  profitAmount: number;
  balance: {
    balanceUsdt: number;
    balanceText: string;
    totalBalanceUsdt: number;
    frozenBalanceUsdt: number;
    availableBalanceUsdt: number;
  };
};

const configApiUrl = '/api/v1/miniapp/casino/config';
const spinApiUrl = '/api/v1/miniapp/casino/spin';
const baseSymbols: SymbolId[] = ['seven', 'lemon', 'star', 'bar', 'diamond', 'cherry'];
const betStepOptions = [0.5, 1, 5];
const reelOrder: ReelId[] = ['left', 'center', 'right'];
const isSpinning = ref(false);
const leverPhase = ref(0);
const windowInteractionMode = ref<WindowInteractionMode>('idle');
const windowFrame = ref({
  x: 32,
  y: 112,
  width: 860,
  height: 760,
});
const windowPointerStart = ref({
  pointerX: 0,
  pointerY: 0,
  x: 0,
  y: 0,
  width: 0,
  height: 0,
});
const spinningReels = ref<Record<ReelId, boolean>>({
  left: false,
  center: false,
  right: false,
});
const reelSymbols = ref<Record<ReelId, SymbolId>>({
  left: 'seven',
  center: 'seven',
  right: 'seven',
});
const errorMessage = ref('');
const spinMessage = ref('');
const spinMessageTone = ref<'info' | 'win'>('info');
const config = ref<MiniAppCasinoConfig>({
  rtp: 97,
  minBet: 0.2,
  maxBet: 100,
  betStepMin: 0.2,
  betStepMax: 0.5,
});
const bet = ref(config.value.minBet);
const selectedBetStep = ref(betStepOptions[0] ?? config.value.betStepMax);
const spinTimers: Partial<Record<ReelId, number>> = {};

const activeStep = computed(() => selectedBetStep.value);
const miniAppWindowStyle = computed(() => ({
  width: `${windowFrame.value.width}px`,
  height: `${windowFrame.value.height}px`,
  transform: `translate3d(${windowFrame.value.x}px, ${windowFrame.value.y}px, 0)`,
}));

const formatUsdtValue = (value: number) =>
  new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 2 }).format(Number(value) || 0);

const formatStepValue = (value: number) => (Number.isInteger(value) ? String(value) : formatUsdtValue(value));

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

const minWindowWidth = () => Math.min(420, Math.max(320, window.innerWidth - 24));
const minWindowHeight = () => Math.min(520, Math.max(420, window.innerHeight - 24));

const constrainWindowFrame = () => {
  const viewportWidth = window.innerWidth || 1024;
  const viewportHeight = window.innerHeight || 768;
  const minWidth = minWindowWidth();
  const minHeight = minWindowHeight();
  const width = clamp(windowFrame.value.width, minWidth, Math.max(minWidth, viewportWidth - 24));
  const height = clamp(windowFrame.value.height, minHeight, Math.max(minHeight, viewportHeight - 24));
  const x = clamp(windowFrame.value.x, 12, Math.max(12, viewportWidth - width - 12));
  const y = clamp(windowFrame.value.y, 12, Math.max(12, viewportHeight - 64));

  windowFrame.value = { x, y, width, height };
};

const resetMiniAppWindow = () => {
  const viewportWidth = window.innerWidth || 1024;
  const viewportHeight = window.innerHeight || 768;
  const width = Math.min(860, Math.max(minWindowWidth(), viewportWidth - 48));
  const height = Math.min(760, Math.max(minWindowHeight(), viewportHeight - 140));
  windowFrame.value = {
    x: Math.max(12, Math.round((viewportWidth - width) / 2)),
    y: Math.max(90, Math.round((viewportHeight - height) / 2)),
    width,
    height,
  };
  constrainWindowFrame();
};

const beginWindowInteraction = (event: PointerEvent, mode: WindowInteractionMode) => {
  windowInteractionMode.value = mode;
  windowPointerStart.value = {
    pointerX: event.clientX,
    pointerY: event.clientY,
    x: windowFrame.value.x,
    y: windowFrame.value.y,
    width: windowFrame.value.width,
    height: windowFrame.value.height,
  };
  window.addEventListener('pointermove', handleWindowPointerMove);
  window.addEventListener('pointerup', stopWindowInteraction);
  window.addEventListener('pointercancel', stopWindowInteraction);
  event.preventDefault();
};

const startWindowDrag = (event: PointerEvent) => {
  if (event.target instanceof HTMLElement && event.target.closest('button, input, a')) return;
  beginWindowInteraction(event, 'drag');
};

const startWindowResize = (event: PointerEvent) => {
  beginWindowInteraction(event, 'resize');
};

const handleWindowPointerMove = (event: PointerEvent) => {
  if (windowInteractionMode.value === 'idle') return;

  const deltaX = event.clientX - windowPointerStart.value.pointerX;
  const deltaY = event.clientY - windowPointerStart.value.pointerY;

  if (windowInteractionMode.value === 'drag') {
    windowFrame.value = {
      ...windowFrame.value,
      x: windowPointerStart.value.x + deltaX,
      y: windowPointerStart.value.y + deltaY,
    };
  } else {
    windowFrame.value = {
      ...windowFrame.value,
      width: windowPointerStart.value.width + deltaX,
      height: windowPointerStart.value.height + deltaY,
    };
  }

  constrainWindowFrame();
};

const stopWindowInteraction = () => {
  windowInteractionMode.value = 'idle';
  window.removeEventListener('pointermove', handleWindowPointerMove);
  window.removeEventListener('pointerup', stopWindowInteraction);
  window.removeEventListener('pointercancel', stopWindowInteraction);
};

const clampBet = (nextValue: number) => {
  const normalized = Math.max(config.value.minBet, Math.min(config.value.maxBet, nextValue));
  return Number(normalized.toFixed(2));
};

const applyConfig = (payload?: Partial<MiniAppCasinoResponse>) => {
  if (!payload?.config) return;
  config.value = {
    rtp: Number(payload.config.rtp ?? 97),
    minBet: Number(payload.config.minBet ?? 0.2),
    maxBet: Number(payload.config.maxBet ?? 100),
    betStepMin: Number(payload.config.betStepMin ?? 0.2),
    betStepMax: Number(payload.config.betStepMax ?? 0.5),
  };
  bet.value = clampBet(bet.value);
};

const loadConfig = async () => {
  try {
    const response = await api.get(configApiUrl);
    applyConfig(response.data);
    errorMessage.value = '';
  } catch (error) {
    errorMessage.value = axios.isAxiosError(error)
      ? String(error.response?.data?.detail ?? 'Failed to load mini app config.')
      : 'Failed to load mini app config.';
  }
};

const changeBet = (direction: number) => {
  bet.value = clampBet(bet.value + direction * activeStep.value);
};

const selectBetStep = (step: number) => {
  selectedBetStep.value = step;
};

const randomSymbol = (): SymbolId => baseSymbols[Math.floor(Math.random() * baseSymbols.length)] ?? 'seven';

const normalizeSymbol = (value: string | undefined): SymbolId => {
  const symbol = String(value ?? '');
  return baseSymbols.includes(symbol as SymbolId) ? (symbol as SymbolId) : randomSymbol();
};

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

const resetLever = async () => {
  await wait(65);
  leverPhase.value = 3;
  await wait(65);
  leverPhase.value = 2;
  await wait(65);
  leverPhase.value = 1;
  await wait(65);
  leverPhase.value = 0;
};

const clearReelTimers = () => {
  reelOrder.forEach((reel) => {
    const timer = spinTimers[reel];
    if (timer) {
      window.clearInterval(timer);
      delete spinTimers[reel];
    }
  });
  spinningReels.value = {
    left: false,
    center: false,
    right: false,
  };
};

const startReel = (reel: ReelId, intervalMs: number) => {
  spinningReels.value[reel] = true;
  spinTimers[reel] = window.setInterval(() => {
    let next = randomSymbol();
    while (next === reelSymbols.value[reel]) {
      next = randomSymbol();
    }
    reelSymbols.value[reel] = next;
  }, intervalMs);
};

const stopReel = async (reel: ReelId, finalSymbol: SymbolId) => {
  await wait(120);
  const timer = spinTimers[reel];
  if (timer) {
    window.clearInterval(timer);
    delete spinTimers[reel];
  }
  reelSymbols.value[reel] = finalSymbol;
  spinningReels.value[reel] = false;
};

const spin = async () => {
  if (isSpinning.value) return;

  errorMessage.value = '';
  spinMessage.value = '';
  const requestedBet = bet.value;

  leverPhase.value = 1;
  await wait(65);
  leverPhase.value = 2;
  await wait(65);
  leverPhase.value = 3;
  await wait(65);
  isSpinning.value = true;
  spinningReels.value = {
    left: true,
    center: true,
    right: true,
  };

  startReel('left', 90);
  startReel('center', 105);
  startReel('right', 120);

  let spinResult: MiniAppCasinoSpinResponse;
  try {
    const [response] = await Promise.all([
      api.post<MiniAppCasinoSpinResponse>(spinApiUrl, { betAmount: requestedBet }),
      wait(950),
    ]);
    spinResult = response.data;
  } catch (error) {
    clearReelTimers();
    isSpinning.value = false;
    errorMessage.value = axios.isAxiosError(error)
      ? String(error.response?.data?.detail ?? (error.request ? 'API мини-казино не отвечает. Запустите backend на localhost:8000.' : 'Не удалось выполнить спин.'))
      : 'Не удалось выполнить спин.';
    await resetLever();
    return;
  }

  const leftFinal = normalizeSymbol(spinResult.symbols.left);
  const centerFinal = normalizeSymbol(spinResult.symbols.center);
  const rightFinal = normalizeSymbol(spinResult.symbols.right);

  await wait(250);
  leverPhase.value = 4;
  await stopReel('left', leftFinal);
  await wait(260);
  leverPhase.value = 5;
  await stopReel('center', centerFinal);
  await wait(320);
  leverPhase.value = 4;
  await stopReel('right', rightFinal);
  isSpinning.value = false;

  if (spinResult.isWin) {
    spinMessageTone.value = 'win';
    spinMessage.value = `Выигрыш ${formatUsdtValue(spinResult.payoutAmount)} USDT x${formatUsdtValue(spinResult.multiplier)}. Баланс: ${spinResult.balance.balanceText}`;
  } else {
    spinMessageTone.value = 'info';
    spinMessage.value = `Ставка ${formatUsdtValue(spinResult.betAmount)} USDT списана. Баланс: ${spinResult.balance.balanceText}`;
  }

  window.dispatchEvent(new CustomEvent('primepay:balance-updated', { detail: spinResult.balance }));
  await resetLever();
};

onMounted(async () => {
  resetMiniAppWindow();
  window.addEventListener('resize', constrainWindowFrame);
  await loadConfig();
});

onBeforeUnmount(() => {
  stopWindowInteraction();
  window.removeEventListener('resize', constrainWindowFrame);
});
</script>

<style scoped>
.miniapp-shell {
  min-height: calc(100vh - 150px);
  position: relative;
  overflow: hidden;
  padding: 0;
  background:
    radial-gradient(circle at 50% 0%, rgba(255, 205, 115, 0.12), transparent 34%),
    radial-gradient(circle at 88% 78%, rgba(227, 79, 79, 0.14), transparent 28%);
}

.miniapp-window {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1180;
  display: flex;
  min-width: 320px;
  min-height: 420px;
  max-width: calc(100vw - 24px);
  max-height: calc(100vh - 24px);
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(255, 211, 145, 0.18);
  border-radius: 30px;
  background: linear-gradient(180deg, rgba(35, 13, 18, 0.98) 0%, rgba(15, 5, 9, 0.98) 100%);
  box-shadow:
    0 28px 70px rgba(0, 0, 0, 0.48),
    inset 0 1px 0 rgba(255, 255, 255, 0.12);
  will-change: transform, width, height;
}

.miniapp-window.is-window-moving {
  user-select: none;
  box-shadow:
    0 34px 88px rgba(0, 0, 0, 0.58),
    0 0 0 1px rgba(255, 211, 145, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.12);
}

.miniapp-window-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 16px 14px 18px;
  border-bottom: 1px solid rgba(255, 211, 145, 0.14);
  background:
    linear-gradient(90deg, rgba(255, 218, 151, 0.1), transparent 34%),
    rgba(255, 255, 255, 0.03);
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.miniapp-window.is-window-moving .miniapp-window-bar {
  cursor: grabbing;
}

.miniapp-window-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.miniapp-window-brand > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.miniapp-window-brand span {
  color: #d7b089;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.miniapp-window-brand strong {
  overflow: hidden;
  color: #fff3d6;
  font-size: 18px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.miniapp-window-icon {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px;
  padding: 8px;
  border-radius: 14px;
  background: linear-gradient(145deg, #f3d79d 0%, #b06e31 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.42), 0 12px 24px rgba(0, 0, 0, 0.28);
}

.miniapp-window-icon span {
  border-radius: 5px;
  background:
    radial-gradient(circle at 35% 30%, rgba(255, 255, 255, 0.7), transparent 34%),
    linear-gradient(180deg, #653b86 0%, #2d164e 100%);
}

.miniapp-window-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 18px;
  background:
    radial-gradient(circle at top, rgba(255, 205, 115, 0.14), transparent 28%),
    radial-gradient(circle at bottom right, rgba(227, 79, 79, 0.12), transparent 24%),
    linear-gradient(180deg, #1f0d0f 0%, #14070b 54%, #080306 100%);
}

.miniapp-resize-handle {
  position: absolute;
  right: 10px;
  bottom: 10px;
  z-index: 5;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 10px;
  background:
    linear-gradient(135deg, transparent 0 42%, rgba(255, 224, 164, 0.72) 42% 50%, transparent 50% 58%, rgba(255, 224, 164, 0.46) 58% 66%, transparent 66%),
    rgba(255, 255, 255, 0.04);
  cursor: nwse-resize;
  touch-action: none;
}

.miniapp-hero {
  max-width: 920px;
  margin: 0 auto 24px;
  text-align: center;
}

.miniapp-badge {
  display: inline-flex;
  padding: 10px 16px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  color: #ffd48d;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.miniapp-title {
  margin: 18px 0 0;
  color: #fff7ea;
  font-size: 56px;
  font-weight: 900;
  line-height: 1;
}

.miniapp-subtitle {
  max-width: 680px;
  margin: 16px auto 0;
  color: #d2bbb4;
  font-size: 17px;
  line-height: 1.6;
}

.miniapp-alert {
  margin: 0 0 18px;
  border-radius: 16px;
  padding: 14px 16px;
  font-size: 14px;
  font-weight: 700;
}

.miniapp-alert-danger {
  border: 1px solid rgba(220, 92, 92, 0.24);
  background: rgba(120, 24, 24, 0.18);
  color: #f3b7b7;
}

.miniapp-alert-success {
  border: 1px solid rgba(111, 214, 124, 0.24);
  background: rgba(44, 105, 52, 0.18);
  color: #c6ffc9;
}

.miniapp-alert-info {
  border: 1px solid rgba(255, 211, 145, 0.18);
  background: rgba(255, 211, 145, 0.08);
  color: #ffe3ad;
}

.miniapp-machine-wrap {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.slot-machine {
  position: relative;
  padding: 28px 28px 46px;
  border-radius: 40px;
  border: 1px solid rgba(255, 208, 146, 0.18);
  background:
    radial-gradient(circle at top, rgba(255, 216, 154, 0.22), transparent 28%),
    linear-gradient(180deg, #b92935 0%, #7d0f1d 35%, #520915 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    inset 0 -18px 30px rgba(40, 0, 8, 0.25),
    0 28px 60px rgba(0, 0, 0, 0.4);
}

.slot-marquee {
  display: grid;
  grid-template-columns: repeat(14, 1fr);
  gap: 10px;
  margin-bottom: 18px;
}

.slot-lamp {
  height: 14px;
  border-radius: 999px;
  background: radial-gradient(circle, #fff7d3 0%, #ffd070 58%, #ff8b42 100%);
  box-shadow: 0 0 14px rgba(255, 184, 92, 0.55);
}

.slot-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}

.slot-brand {
  color: #fff5d1;
  font-size: 18px;
  font-weight: 900;
  letter-spacing: 0.16em;
}

.slot-display {
  min-width: 180px;
  padding: 12px 16px;
  border: 1px solid rgba(255, 223, 162, 0.2);
  border-radius: 18px;
  background: linear-gradient(180deg, #2c0612 0%, #14030a 100%);
  text-align: right;
}

.slot-display span {
  display: block;
  color: #d8b7a7;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.slot-display strong {
  display: block;
  margin-top: 6px;
  color: #ffe2a4;
  font-size: 24px;
  font-weight: 900;
}

.slot-window-frame {
  --lever-groove-top: 50px;
  --lever-groove-right: 25px;
  --lever-groove-height: 82px;
  --lever-groove-half: 41px;
  --lever-groove-width: 20px;
  --lever-top: 40px;
  --lever-width: 60px;
  --lever-knob-size: 36px;

  position: relative;
  overflow: hidden;
  padding: 18px 74px 18px 18px;
  border-radius: 28px;
  background: linear-gradient(180deg, #f3d9ae 0%, #a66f34 100%);
  box-shadow: inset 0 2px 0 rgba(255, 255, 255, 0.45);
}

.slot-window {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  overflow: hidden;
  border-radius: 22px;
  padding: 16px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.14) 0%, rgba(255, 255, 255, 0.02) 100%),
    linear-gradient(180deg, #42236e 0%, #25124a 100%);
}

.slot-window-gloss {
  position: absolute;
  top: 18px;
  right: 74px;
  bottom: 18px;
  left: 18px;
  border-radius: 22px;
  pointer-events: none;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.18) 0%, rgba(255, 255, 255, 0) 30%);
}

.slot-reel {
  height: 94px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  background: linear-gradient(180deg, #f6f2ff 0%, #d2cdf2 100%);
  box-shadow: inset 0 -14px 20px rgba(92, 71, 154, 0.18);
  position: relative;
}

.slot-reel.is-spinning::before,
.slot-reel.is-spinning::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  height: 22px;
  background: linear-gradient(180deg, rgba(255,255,255,0.2) 0%, rgba(255,255,255,0) 100%);
  animation: reelFlash 220ms linear infinite;
  pointer-events: none;
}

.slot-reel.is-spinning::before {
  top: 8px;
}

.slot-reel.is-spinning::after {
  bottom: 8px;
  transform: rotate(180deg);
}

.slot-symbol {
  height: 94px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  transition: transform 120ms linear, opacity 120ms linear;
}

.slot-reel.is-spinning .slot-symbol {
  transform: scaleY(0.9);
  opacity: 0.92;
}

.symbol-seven {
  display: block;
  color: #cc2e5a;
  font-size: 52px;
  font-weight: 900;
  text-shadow: 0 2px 0 rgba(255, 255, 255, 0.35);
}

.symbol-bar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 10px 18px;
  border-radius: 12px;
  background: linear-gradient(180deg, #161616 0%, #3b3b3b 100%);
  color: #fff3d6;
  font-size: 26px;
  font-weight: 900;
  letter-spacing: 0.12em;
}

.symbol-diamond {
  display: block;
  width: 38px;
  height: 38px;
  transform: rotate(45deg);
  border-radius: 8px;
  background: linear-gradient(180deg, #84d7ff 0%, #3387ff 100%);
  box-shadow: 0 0 18px rgba(74, 144, 255, 0.3);
}

.symbol-star {
  display: block;
  color: #f0bf4a;
  font-size: 48px;
  line-height: 1;
  text-shadow: 0 2px 0 rgba(255, 255, 255, 0.25);
}

.symbol-lemon {
  display: block;
  width: 48px;
  height: 34px;
  border-radius: 999px;
  background: linear-gradient(180deg, #ffe47d 0%, #ffc42c 100%);
  box-shadow: inset 0 -6px 10px rgba(201, 141, 13, 0.2);
  position: relative;
}

.symbol-lemon::before {
  content: '';
  position: absolute;
  top: -7px;
  left: 18px;
  width: 12px;
  height: 10px;
  border-radius: 12px 12px 0 12px;
  background: #6dbd55;
  transform: rotate(-24deg);
}

.symbol-cherry {
  display: block;
  position: relative;
  width: 54px;
  height: 42px;
}

.symbol-cherry::before {
  content: '';
  position: absolute;
  top: 2px;
  left: 24px;
  width: 20px;
  height: 16px;
  border-top: 3px solid #5ca14b;
  border-right: 3px solid #5ca14b;
  border-radius: 0 16px 0 0;
  transform: rotate(-12deg);
}

.symbol-cherry i {
  position: absolute;
  bottom: 0;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: radial-gradient(circle at 35% 35%, #ff9db2 0%, #ff4d72 48%, #ba143f 100%);
}

.symbol-cherry i:first-child {
  left: 6px;
}

.symbol-cherry i:last-child {
  right: 4px;
}

.slot-lever-groove {
  position: absolute;
  top: var(--lever-groove-top);
  right: var(--lever-groove-right);
  height: var(--lever-groove-height);
  width: var(--lever-groove-width);
  border-radius: 999px;
  background: linear-gradient(180deg, rgba(82, 44, 111, 0.84) 0%, rgba(44, 20, 78, 0.92) 100%);
  box-shadow:
    inset 0 2px 4px rgba(255, 255, 255, 0.22),
    inset 0 -8px 10px rgba(26, 7, 45, 0.35);
  z-index: 2;
}

.slot-lever {
  position: absolute;
  top: var(--lever-top);
  right: calc(var(--lever-groove-right) + 10px - 30px);
  width: var(--lever-width);
  height: calc(var(--lever-groove-top) + var(--lever-groove-height) - var(--lever-top));
  border: 0;
  background: transparent;
  cursor: pointer;
  transform-origin: top center;
  z-index: 4;
}

.slot-lever-phase-1 {
  transform: none;
}

.slot-lever-phase-2 {
  transform: none;
}

.slot-lever-phase-3 {
  transform: none;
}

.slot-lever-phase-4 {
  transform: none;
}

.slot-lever-phase-5 {
  transform: none;
}

.slot-lever.is-disabled {
  cursor: default;
}

.slot-lever-stem {
  position: absolute;
  top: auto;
  bottom: 0;
  left: 19px;
  width: 22px;
  height: 25px;
  border-radius: 999px;
  background: linear-gradient(180deg, #ebe7f4 0%, #aaa4bf 100%);
  box-shadow: inset 0 -10px 16px rgba(54, 42, 72, 0.18);
  transition: height 0.1s cubic-bezier(0.2, 0.86, 0.22, 1);
  z-index: 3;
}

.slot-lever-phase-1 .slot-lever-stem {
  height: 22px;
}

.slot-lever-phase-2 .slot-lever-stem {
  height: 18px;
}

.slot-lever-phase-3 .slot-lever-stem {
  height: 14px;
}

.slot-lever-phase-4 .slot-lever-stem {
  height: 10px;
}

.slot-lever-phase-5 .slot-lever-stem {
  height: 8px;
}

.slot-lever-knob {
  position: absolute;
  top: calc(var(--lever-groove-top) + var(--lever-groove-half) - 18px - var(--lever-top));
  left: 12px;
  width: var(--lever-knob-size);
  height: var(--lever-knob-size);
  border-radius: 999px;
  background: radial-gradient(circle at 35% 35%, #ff96a7 0%, #ff607c 45%, #bc244a 100%);
  box-shadow: 0 10px 18px rgba(115, 8, 34, 0.35);
  transition: transform 0.1s cubic-bezier(0.2, 0.86, 0.22, 1);
  z-index: 4;
}

.slot-lever-phase-1 .slot-lever-knob {
  transform: translateY(4px);
}

.slot-lever-phase-2 .slot-lever-knob {
  transform: translateY(8px);
}

.slot-lever-phase-3 .slot-lever-knob {
  transform: translateY(12px);
}

.slot-lever-phase-4 .slot-lever-knob {
  transform: translateY(15px);
}

.slot-lever-phase-5 .slot-lever-knob {
  transform: translateY(18px);
}

.slot-base {
  position: absolute;
  left: 8%;
  right: 8%;
  bottom: 12px;
  height: 18px;
  border-radius: 999px;
  background: linear-gradient(180deg, rgba(255, 235, 193, 0.28) 0%, rgba(54, 0, 11, 0.5) 100%);
}

.miniapp-controls {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.miniapp-bet-controls {
  display: grid;
  grid-template-areas: 'step-left minus bet plus step-right';
  grid-template-columns: 82px 64px minmax(180px, 1fr) 64px 82px;
  gap: 10px;
  align-items: stretch;
}

.miniapp-stepper,
.miniapp-step-choice,
.miniapp-spin {
  border: 0;
  cursor: pointer;
  transition: transform 0.18s ease, filter 0.18s ease, opacity 0.18s ease;
}

.miniapp-step-side {
  display: grid;
  grid-template-rows: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.miniapp-step-side-left {
  grid-area: step-left;
}

.miniapp-step-side-right {
  grid-area: step-right;
}

.miniapp-step-choice {
  min-height: 38px;
  border: 1px solid rgba(255, 204, 143, 0.16);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(47, 17, 27, 0.96) 0%, rgba(26, 8, 17, 0.98) 100%);
  color: #ffe0a4;
  font-size: 13px;
  font-weight: 900;
  letter-spacing: 0.04em;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 12px 24px rgba(0, 0, 0, 0.22);
}

.miniapp-step-choice.is-active {
  border-color: rgba(255, 220, 145, 0.84);
  background: linear-gradient(135deg, #ffe092 0%, #ffb451 100%);
  color: #23090f;
  box-shadow: 0 12px 28px rgba(255, 171, 77, 0.28);
}

.miniapp-stepper {
  min-height: 100%;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  font-size: 30px;
  font-weight: 900;
}

.miniapp-stepper-minus {
  grid-area: minus;
}

.miniapp-stepper-plus {
  grid-area: plus;
}

.miniapp-stepper:hover,
.miniapp-step-choice:hover,
.miniapp-spin:hover {
  filter: brightness(1.03);
  transform: translateY(-1px);
}

.miniapp-stepper:disabled,
.miniapp-step-choice:disabled,
.miniapp-spin:disabled {
  cursor: default;
  opacity: 0.6;
  transform: none;
}

.miniapp-bet-box {
  grid-area: bet;
  border: 1px solid rgba(255, 204, 143, 0.14);
  border-radius: 24px;
  background: linear-gradient(180deg, rgba(34, 10, 18, 0.94) 0%, rgba(15, 5, 12, 0.98) 100%);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.35);
  padding: 16px;
  text-align: center;
}

.miniapp-bet-label {
  color: #ffcf7f;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.miniapp-bet-value {
  margin-top: 8px;
  color: #fff1d0;
  font-size: 34px;
  font-weight: 900;
}

.miniapp-bet-step-copy {
  margin-top: 8px;
  color: #d2bbb4;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.miniapp-spin {
  width: 100%;
  padding: 18px 20px;
  border-radius: 20px;
  background: linear-gradient(135deg, #ffe08e 0%, #ff9c52 52%, #ff5d5d 100%);
  color: #22090f;
  font-size: 18px;
  font-weight: 900;
  box-shadow: 0 18px 36px rgba(255, 110, 76, 0.24);
}

@keyframes reelFlash {
  from {
    opacity: 0.15;
  }
  to {
    opacity: 0.45;
  }
}

@keyframes miniappWindowIn {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (max-width: 640px) {
  .miniapp-shell {
    min-height: calc(100vh - 120px);
    padding: 0 0 28px;
  }

  .miniapp-window {
    border-radius: 24px;
  }

  .miniapp-window-bar {
    padding: 12px;
  }

  .miniapp-window-brand strong {
    font-size: 16px;
  }

  .miniapp-window-body {
    padding: 12px;
  }

  .miniapp-title {
    font-size: 42px;
  }

  .slot-machine {
    padding-left: 18px;
    padding-right: 18px;
  }

  .slot-top {
    flex-direction: column;
    align-items: stretch;
  }

  .slot-window {
    gap: 10px;
    padding: 12px;
  }

  .slot-window-frame {
    --lever-groove-top: 42px;
    --lever-groove-right: 18px;
    --lever-groove-height: 72px;
    --lever-groove-half: 36px;

    padding: 12px 58px 12px 12px;
  }

  .slot-window-gloss {
    top: 12px;
    right: 58px;
    bottom: 12px;
    left: 12px;
  }

  .symbol-seven {
    font-size: 44px;
  }

  .symbol-bar {
    font-size: 22px;
  }

  .miniapp-bet-controls {
    grid-template-areas: 'step-left minus bet plus step-right';
    grid-template-columns: 64px 52px minmax(150px, 1fr) 52px 64px;
    gap: 8px;
  }

  .miniapp-step-side {
    grid-template-columns: none;
    grid-template-rows: repeat(3, minmax(0, 1fr));
    gap: 6px;
  }

  .miniapp-step-choice {
    min-height: 34px;
    border-radius: 12px;
    font-size: 12px;
  }

  .miniapp-stepper {
    min-height: 100%;
    font-size: 24px;
  }
}

@media (max-width: 520px) {
  .miniapp-bet-controls {
    grid-template-areas:
      'step-left step-right'
      'bet bet'
      'minus plus';
    grid-template-columns: 1fr 1fr;
  }

  .miniapp-step-side {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    grid-template-rows: none;
  }

  .miniapp-step-choice {
    min-height: 42px;
  }

  .miniapp-stepper {
    min-height: 56px;
  }
}
</style>

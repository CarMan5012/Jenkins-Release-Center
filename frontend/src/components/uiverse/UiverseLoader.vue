<template>
  <div class="uiverse-loader-container" :class="[`uiverse-loader--${size}`]">
    <!-- Style 1: Cyber Ring Pulse -->
    <div v-if="mode === 'ring'" class="uiverse-ring-loader">
      <div></div>
      <div></div>
      <div></div>
      <div></div>
    </div>

    <!-- Style 2: 3D Cubes Spin -->
    <div v-else-if="mode === 'cube'" class="uiverse-cube-loader">
      <div class="cube">
        <div class="side front"></div>
        <div class="side back"></div>
        <div class="side right"></div>
        <div class="side left"></div>
        <div class="side top"></div>
        <div class="side bottom"></div>
      </div>
    </div>

    <!-- Style 3: Orbit Wave -->
    <div v-else class="uiverse-orbit-loader">
      <span class="dot dot-1"></span>
      <span class="dot dot-2"></span>
      <span class="dot dot-3"></span>
    </div>

    <div v-if="text" class="uiverse-loader-text">
      {{ text }}
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    mode?: 'ring' | 'cube' | 'orbit';
    size?: 'sm' | 'md' | 'lg';
    text?: string;
  }>(),
  {
    mode: 'ring',
    size: 'md',
  }
);
</script>

<style scoped>
.uiverse-loader-container {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 12px;
}

.uiverse-loader-text {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted, #6e6e73);
  letter-spacing: 0.01em;
}

/* --- Style 1: Cyber Ring Loader --- */
.uiverse-ring-loader {
  display: inline-block;
  position: relative;
  width: 36px;
  height: 36px;
}
.uiverse-ring-loader div {
  box-sizing: border-box;
  display: block;
  position: absolute;
  width: 30px;
  height: 30px;
  margin: 3px;
  border: 3px solid #0071e3;
  border-radius: 50%;
  animation: ring-spin 1.2s cubic-bezier(0.5, 0, 0.5, 1) infinite;
  border-color: #0071e3 transparent transparent transparent;
}
.uiverse-ring-loader div:nth-child(1) { animation-delay: -0.45s; }
.uiverse-ring-loader div:nth-child(2) { animation-delay: -0.3s; }
.uiverse-ring-loader div:nth-child(3) { animation-delay: -0.15s; }

@keyframes ring-spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* --- Style 2: 3D Cube Loader --- */
.uiverse-cube-loader {
  width: 32px;
  height: 32px;
  perspective: 200px;
}
.uiverse-cube-loader .cube {
  width: 100%;
  height: 100%;
  position: relative;
  transform-style: preserve-3d;
  animation: cube-rotate 2s infinite ease-in-out;
}
.uiverse-cube-loader .side {
  position: absolute;
  width: 100%;
  height: 100%;
  background: rgba(0, 113, 227, 0.75);
  border: 1px solid rgba(255, 255, 255, 0.4);
}
.uiverse-cube-loader .front  { transform: translateZ(16px); }
.uiverse-cube-loader .back   { transform: rotateY(180deg) translateZ(16px); }
.uiverse-cube-loader .right  { transform: rotateY(90deg) translateZ(16px); }
.uiverse-cube-loader .left   { transform: rotateY(-90deg) translateZ(16px); }
.uiverse-cube-loader .top    { transform: rotateX(90deg) translateZ(16px); }
.uiverse-cube-loader .bottom { transform: rotateX(-90deg) translateZ(16px); }

@keyframes cube-rotate {
  0% { transform: rotateX(0deg) rotateY(0deg); }
  50% { transform: rotateX(180deg) rotateY(180deg); }
  100% { transform: rotateX(360deg) rotateY(360deg); }
}

/* --- Style 3: Orbit Loader --- */
.uiverse-orbit-loader {
  display: flex;
  align-items: center;
  gap: 6px;
}
.uiverse-orbit-loader .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #0071e3;
  animation: orbit-bounce 1.4s infinite ease-in-out both;
}
.uiverse-orbit-loader .dot-1 { animation-delay: -0.32s; }
.uiverse-orbit-loader .dot-2 { animation-delay: -0.16s; }

@keyframes orbit-bounce {
  0%, 80%, 100% { transform: scale(0); opacity: 0.3; }
  40% { transform: scale(1); opacity: 1; }
}

/* Scale Sizes */
.uiverse-loader--sm { transform: scale(0.8); }
.uiverse-loader--lg { transform: scale(1.3); }
</style>

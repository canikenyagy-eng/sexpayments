<template>
  <section :class="['space-y-3', $attrs.class as string]">
    <header v-if="label || $slots.label || $slots.actions || help || $slots.help" class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-1.5">
        <h4 class="text-xs font-bold uppercase tracking-wider text-text-muted">
          <slot name="label">{{ label }}</slot>
        </h4>
        <BaseHelp v-if="help || $slots.help" :text="help">
          <slot name="help" />
        </BaseHelp>
      </div>
      <div v-if="$slots.actions" class="flex items-center gap-2">
        <slot name="actions" />
      </div>
    </header>
    <slot />
  </section>
</template>

<script setup lang="ts">
import BaseHelp from './BaseHelp.vue'

/**
 * Bordered-free section with an uppercase label header — used to group
 * form fields inside admin edit modals (see the redesigned merchant
 * modal). Optional ``actions`` slot lands top-right next to the label;
 * ``help`` (prop or named slot) renders a tooltip icon next to the
 * label instead of a separate paragraph of muted text.
 *
 * Inheritance of ``class`` is opt-in via ``$attrs.class`` so callers
 * can extend the spacing without us spreading attrs onto an unrelated
 * inner node.
 */
defineProps<{ label?: string; help?: string }>()
defineOptions({ inheritAttrs: false })
</script>

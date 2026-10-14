ENTRY(Reset_Handler)
MEMORY
{
    /* Sector 7 (128 KB) is an append-only boot journal, never executable code. */
    FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 384K
    RAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 128K
}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS
{
    .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
    .text : { *(.text*) *(.rodata*) KEEP(*(.init)) KEEP(*(.fini)) } > FLASH
    .ARM.extab : { *(.ARM.extab*) } > FLASH
    .ARM.exidx : { __exidx_start = .; *(.ARM.exidx*) __exidx_end = .; } > FLASH
    . = ALIGN(4);
    _sidata = LOADADDR(.data);
    .data : { . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT > FLASH
    .bss : { . = ALIGN(8); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(8); _ebss = .; } > RAM
    .heap_stack (NOLOAD) : { . = ALIGN(8); end = .; _end = .; _heap_start = .; . += 1024; _heap_end = .; . += 8192; } > RAM
    ASSERT(_ebss + 9216 <= _estack, "RAM overflow: retain 8 KB stack and 1 KB libc heap")
    /DISCARD/ : { *(.note.GNU-stack) }
}

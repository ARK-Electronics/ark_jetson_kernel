/*
 * Native helpers for hadron_view.py, built on first run with:
 * cc -O3 -shared -fPIC -o libeorender.so eorender.c
 */
#include <stddef.h>
#include <stdint.h>

/* The ARM generic timer, which counts the Tegra TSC that VI timestamps use. */
uint64_t cntvct(void)
{
	uint64_t ticks;

	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(ticks));
	return ticks;
}

uint64_t cntfrq(void)
{
	uint64_t hz;

	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(hz));
	return hz;
}

/*
 * EO render: 2x2 Bayer binning (BGGR) of RAW10 frames stored MSB-aligned in
 * 16-bit words, through a 1024-entry LUT per channel, to RGBA.
 */
void render_rows(const uint16_t *src, size_t stride, uint32_t *out, int out_w, int out_rows,
		 const uint8_t *lut_r, const uint8_t *lut_g, const uint8_t *lut_b)
{
	for (int y = 0; y < out_rows; y++) {
		const uint16_t *even = src + 2 * y * stride;	/* B G B G ... */
		const uint16_t *odd = even + stride;		/* G R G R ... */
		uint32_t *o = out + (size_t)y * out_w;

		for (int x = 0; x < out_w; x++)
			o[x] = lut_r[odd[2 * x + 1] >> 6] | lut_g[even[2 * x + 1] >> 6] << 8 |
			       (uint32_t)lut_b[even[2 * x] >> 6] << 16 | 0xff000000u;
	}
}

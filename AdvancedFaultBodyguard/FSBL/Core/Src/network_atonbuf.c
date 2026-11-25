#include "bodyguard_ml_config.h"
#if BODYGUARD_ML_BACKEND == BODYGUARD_ML_NPU
#include "network_atonbuf.h"

#include <string.h>
#include "stm32n6xx_hal.h"

#define NETWORK_ATONBUF_AXISRAM5_ADDR 0x342E0000UL

#include "network_atonbuf_data.inc"

void NetworkAtonbuf_Load(void)
{
  memcpy((void *)NETWORK_ATONBUF_AXISRAM5_ADDR,
         network_atonbuf_AXISRAM5_raw,
         network_atonbuf_AXISRAM5_raw_len);

  uintptr_t start = NETWORK_ATONBUF_AXISRAM5_ADDR & ~31UL;
  uintptr_t end = (NETWORK_ATONBUF_AXISRAM5_ADDR + network_atonbuf_AXISRAM5_raw_len + 31UL) & ~31UL;

  SCB_CleanInvalidateDCache_by_Addr((uint32_t *)start, (int32_t)(end - start));
}

#endif /* selected ML backend */

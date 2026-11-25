#ifndef BODYGUARD_ML_H
#define BODYGUARD_ML_H
#include "bodyguard_ml_config.h"
#include "network_preprocessing.h"

enum {
    BODYGUARD_ML_OK = 0,
    BODYGUARD_ML_NOT_READY = -1,
    BODYGUARD_ML_INIT_ERROR = -2,
    BODYGUARD_ML_RUN_ERROR = -3,
    BODYGUARD_ML_TIMEOUT = -4,
    BODYGUARD_ML_BAD_ARGUMENT = -5,
    BODYGUARD_ML_PREPROCESS_ERROR = -6
};

typedef struct {
    int32_t status;
    uint32_t backend;
    uint32_t predicted_class; /* 0: autoindotto; 1: indotto; UINT32_MAX: invalid */
    int8_t quantized_output[2];
    float probability[2];
    uint32_t preprocess_ms;
    uint32_t inference_ms;
    nn_preprocess_info_t preprocessing;
} bodyguard_ml_result_t;

/* Call after clocks, RAMCFG and system isolation initialization. Single caller. */
int32_t BodyguardML_Init(void);
/* Quantized-input API for comparisons using exactly the same input tensor. */
int32_t BodyguardML_RunInt8(const int8_t input[NN_PREPROCESS_INPUT_FEATURES],
                          bodyguard_ml_result_t *result);
int32_t BodyguardML_RunADC(const uint8_t adc[BODYGUARD_ADC_SAMPLES],
                         bodyguard_ml_result_t *result);
#endif

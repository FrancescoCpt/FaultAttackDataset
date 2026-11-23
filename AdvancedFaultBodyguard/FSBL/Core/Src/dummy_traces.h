#ifndef DUMMY_TRACES_H
#define DUMMY_TRACES_H

#include <stdint.h>
#include <stddef.h>

#include "network_preprocessing.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DUMMY_TRACE_FEATURES NN_PREPROCESS_INPUT_FEATURES
#define DUMMY_TRACE_COUNT    2u

typedef enum
{
    DUMMY_TRACE_LABEL_INDOTTO = 0,
    DUMMY_TRACE_LABEL_AUTOINDOTTO = 1
} dummy_trace_label_t;

typedef struct
{
    const char *dataset;
    const char *label;
    const float *features_f32;
    const int8_t *features_int8;
    size_t feature_count;
} dummy_trace_t;

extern const float DUMMY_TRACE_INDOTTO_F32[DUMMY_TRACE_FEATURES];
extern const int8_t DUMMY_TRACE_INDOTTO_INT8[DUMMY_TRACE_FEATURES];
extern const float DUMMY_TRACE_AUTOINDOTTO_F32[DUMMY_TRACE_FEATURES];
extern const int8_t DUMMY_TRACE_AUTOINDOTTO_INT8[DUMMY_TRACE_FEATURES];

extern const dummy_trace_t DUMMY_TRACES[DUMMY_TRACE_COUNT];

#ifdef __cplusplus
}
#endif

#endif /* DUMMY_TRACES_H */

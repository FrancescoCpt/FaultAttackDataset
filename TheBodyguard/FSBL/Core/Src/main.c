/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file           : main.c
 * @brief          : Main program body  (DUAL REGULAR-SIMULTANEOUS variant)
 ******************************************************************************
 * @attention
 *
 * Copyright (c) 2026 STMicroelectronics.
 * All rights reserved.
 *
 * This software is licensed under terms that can be found in the LICENSE file
 * in the root directory of this software component.
 * If no LICENSE file comes with this software, it is provided AS-IS.
 *
 ******************************************************************************
 */
/* USER CODE END Header */
/*
 * ============================================================================
 *  DUAL REGULAR-SIMULTANEOUS MODE
 * ============================================================================
 *  ADC1 (master) and ADC2 (slave) are started by a SINGLE call and sample at
 *  the same instants. One DMA (the master's, GPDMA1_Channel1) reads the common
 *  data register ADC12_COMMON->CDR into adc_dual_buf. Each 32-bit word holds:
 *
 *      bits [15:0]  = ADC1 master  (8-bit code in the low byte)
 *      bits [31:16] = ADC2 slave   (8-bit code in the low byte of the high half)
 *
 *  Data format used: ADC_DUALMODEDATAFORMAT_32_10_BITS (DAMDF=0b10, 16-bit
 *  half-words). The 8-bit *packed* format (DAMDF=0b11) is deliberately NOT used
 *  here: it is reported to leave the slave half as junk on this ADC IP. The
 *  16-bit format is the reliable one; we just mask the low byte of each half.
 *
 *  MSP NOTE: no change to stm32n6xx_hal_msp.c is required. GPDMA1_Channel1
 *  (linked to ADC1) is already WORD/WORD, SrcInc FIXED, DestInc INCREMENTED,
 *  which is exactly what reading the 32-bit CDR needs. GPDMA1_Channel0 (ADC2)
 *  simply stays idle — it is not started in dual mode.
 * ============================================================================
 */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef enum {
	IDLE,
	ACQUISITION,
	WAIT_FETCH1,
	WAIT_FETCH2,
	SEND1,
	SEND2
} State_t;

#define start_char 'A'
#define FETCH1_char 'X'
#define FETCH2_char 'Y'
#define RESET_char 'R'

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define NUM_SAMPLES 6000

/*
 * PRE_TRIGGER_GUARD
 * -----------------
 * Busy-wait iterations between starting the dual acquisition and raising the
 * TRIGGER edge. Even with a single start, the ADC/DMA front-end needs a moment
 * to be actively converting before the event begins, otherwise the first
 * samples of the window fall outside the capture. Tune while watching the plot:
 *   - too small -> the beginning of the signal is still missing
 *   - too large -> the window fills with baseline and the END gets pushed out
 * Keep it MUCH smaller than the full 6000-sample window.
 */
#define PRE_TRIGGER_GUARD 3000U
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;
DMA_HandleTypeDef handle_GPDMA1_Channel1;
DMA_HandleTypeDef handle_GPDMA1_Channel0;

CACHEAXI_HandleTypeDef hcacheaxi;

UART_HandleTypeDef hlpuart1;

RAMCFG_HandleTypeDef hramcfg_SRAM2;
RAMCFG_HandleTypeDef hramcfg_SRAM3;
RAMCFG_HandleTypeDef hramcfg_SRAM4;
RAMCFG_HandleTypeDef hramcfg_SRAM5;
RAMCFG_HandleTypeDef hramcfg_SRAM6;

/* USER CODE BEGIN PV */
volatile uint8_t flag_button = 0;
volatile uint8_t flag_uart   = 0;
volatile uint8_t adc_done    = 0;   /* set by ADC2 callback (unused in dual mode) */
volatile uint8_t adc_done2   = 0;   /* set by ADC1 (master) callback -> the one we wait on */
volatile uint8_t prima_acq   = 0;
volatile uint8_t prima_acq2  = 0;

State_t state = IDLE;

char buf_tras[16];
char buf_tras2[16];

volatile uint32_t adc_err = 0;
volatile uint32_t dma_err = 0;


volatile uint32_t adc_err2 = 0;
volatile uint32_t dma_err2 = 0;

/* Dual-mode capture buffer: each 32-bit word = { slave[31:16], master[15:0] }.
 * 32-bit words because the DMA reads the 32-bit CDR. Non-cacheable so the DMA
 * writes are visible to the CPU without depending on cache state; 32-byte
 * aligned as required by the cache-maintenance / DMA path. */
uint32_t adc_dual_buf[NUM_SAMPLES] __NON_CACHEABLE __attribute__((aligned(32)));

uint8_t uart_tx_buf[NUM_SAMPLES];   /* ADC2 (slave)  — transmitted first  */
uint8_t uart_tx_buf2[NUM_SAMPLES];  /* ADC1 (master) — transmitted second */


uint8_t rx_byte; // Buffer per ricevere un singolo carattere da LabVIEW
char rx_buffer[100];        // Buffer grande per l'intero messaggio (fino a 100 caratteri)
volatile uint8_t rx_indice = 0;

uint32_t last_blink_time = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void SystemIsolation_Config(void);
static void MX_GPIO_Init(void);
static void MX_GPDMA1_Init(void);
static void MX_ADC1_Init(void);
static void MX_ADC2_Init(void);
static void MX_CACHEAXI_Init(void);
static void MX_LPUART1_UART_Init(void);
static void MX_RAMCFG_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
 * @brief  The application entry point.
 * @retval int
 */
int main(void)
{

	/* USER CODE BEGIN 1 */

	/* USER CODE END 1 */

	/* MCU Configuration--------------------------------------------------------*/
	// BODYGUARD
	HAL_Init();

	/* USER CODE BEGIN Init */

	/* USER CODE END Init */

	/* Configure the system clock */
	SystemClock_Config();

	/* USER CODE BEGIN SysInit */
	__HAL_RCC_PWR_CLK_ENABLE(); //abilito l'alimentazione analogica dell'adc
	SET_BIT(PWR->SVMCR3, PWR_SVMCR3_ASV); //settando il bit ASV dichiara attiva
	//l'alim776entazione dell'ADC

	//configurazione del RIFSC (controller che decide chi può accedere a cosa
	//separa risorse secure da not secure. senza questo blocco leggo val. errati)
	__HAL_RCC_RIFSC_CLK_ENABLE(); //abilito il clock del controller
	SET_BIT(RIFSC->RISC_SECCFGRx[2],  (1UL << 0)); //ADC in contesto secure
	SET_BIT(RIFSC->RISC_PRIVCFGRx[2], (1UL << 0)); //adc accesso privilegiato

	/* USER CODE END SysInit */

	/* Initialize all configured peripherals */
	MX_GPIO_Init();
	MX_GPDMA1_Init();
	MX_ADC1_Init();
	MX_ADC2_Init();
	MX_CACHEAXI_Init();
	MX_LPUART1_UART_Init();
	MX_RAMCFG_Init();
	SystemIsolation_Config();
	/* USER CODE BEGIN 2 */
	HAL_UART_Receive_IT(&hlpuart1, &rx_byte, 1);	// si mette pronto in attesa che arrivino caratteri
	HAL_GPIO_WritePin(RED_LED_GPIO_Port, RED_LED_Pin, GPIO_PIN_SET);
	HAL_GPIO_WritePin(GREEN_LED_GPIO_Port, GREEN_LED_Pin, GPIO_PIN_SET);

	/* USER CODE END 2 */

	/* Infinite loop */
	/* USER CODE BEGIN WHILE */
	while (1)
	{
		/* USER CODE END WHILE */

		/* USER CODE BEGIN 3 */

		switch(state)
		{

		case IDLE :
			if (HAL_GetTick() - last_blink_time >= 500)
			{
				last_blink_time = HAL_GetTick();

				HAL_GPIO_TogglePin(BLUE_LED_GPIO_Port ,BLUE_LED_Pin );
			}

			if (flag_button == 1)
			{
				flag_button = 0;
				HAL_GPIO_WritePin(BLUE_LED_GPIO_Port, BLUE_LED_Pin, GPIO_PIN_RESET);
				state = ACQUISITION;
			}
			break;


		case ACQUISITION:
		{
			adc_done  = 0;
			adc_err   = 0;
			dma_err   = 0;
			adc_done2 = 0;
			adc_err2  = 0;
			dma_err2  = 0;

			__HAL_ADC_CLEAR_FLAG(&hadc2, ADC_FLAG_EOC | ADC_FLAG_EOS | ADC_FLAG_OVR);
			__HAL_ADC_CLEAR_FLAG(&hadc1, ADC_FLAG_EOC | ADC_FLAG_EOS | ADC_FLAG_OVR);
			//pulisco i flag degli adc

			if (prima_acq) {
				/* Re-arm: one stop for the whole dual group, then de-init /
				 * re-init both ADCs (master first, so its multimode config is
				 * applied before the slave's channel config). */
				HAL_ADCEx_MultiModeStop_DMA(&hadc1);

				HAL_ADC_DeInit(&hadc1);
				HAL_ADC_DeInit(&hadc2);
				MX_ADC1_Init();   /* master: also re-applies the dual-mode config */
				MX_ADC2_Init();   /* slave  */
			}

			/* Single start: arms BOTH ADCs + the master DMA reading the CDR. */
			HAL_StatusTypeDef ret = HAL_ADCEx_MultiModeStart_DMA(
					&hadc1, (uint32_t *)adc_dual_buf, NUM_SAMPLES);

			if (ret != HAL_OK)
			{
				adc_err = HAL_ADC_GetError(&hadc1);
				Error_Handler();
			}

			/* ----------------------------------------------------------------
			 * PRE-TRIGGER GUARD
			 * Make sure the ADC/DMA is actually converting before we launch the
			 * event, so the start of the trace is not clipped.
			 * ---------------------------------------------------------------- */
			for (volatile uint32_t d = 0; d < PRE_TRIGGER_GUARD; ++d)
			{
				__NOP();
			}

			HAL_GPIO_WritePin(RED_LED_GPIO_Port, RED_LED_Pin, GPIO_PIN_RESET);
			HAL_GPIO_WritePin(TRIGGER_GPIO_Port, TRIGGER_Pin, GPIO_PIN_SET);

			/* In dual mode only the MASTER (ADC1) completion callback fires,
			 * so we wait on adc_done2 alone. */
			while (!adc_done2)
			{
				__WFI();
			}

			HAL_GPIO_WritePin(RED_LED_GPIO_Port, RED_LED_Pin, GPIO_PIN_SET);
			HAL_GPIO_WritePin(TRIGGER_GPIO_Port, TRIGGER_Pin, GPIO_PIN_RESET);


			SCB_InvalidateDCache_by_Addr((uint32_t *)adc_dual_buf,
					NUM_SAMPLES * sizeof(uint32_t));
			//toglie dalla d-cache la vecchia copia del buffer, così la cpu legge
			//i campioni reali scritti dal dma in ram

			for (uint32_t i = 0; i < NUM_SAMPLES; i++)
			{
				uint32_t w = adc_dual_buf[i];
				uart_tx_buf2[i] = (uint8_t)( w         & 0xFFU);  /* ADC1 master -> CDR[15:0]  */
				uart_tx_buf[i]  = (uint8_t)((w >> 16)  & 0xFFU);  /* ADC2 slave  -> CDR[31:16] */
			}


			char c = 'E';
			HAL_UART_Transmit(&hlpuart1, (uint8_t *)&c, 1, HAL_MAX_DELAY);


			flag_uart = 0;
			prima_acq = 1;

			state = WAIT_FETCH1;


			break;
		}


		case WAIT_FETCH1:
			if (flag_uart == 1)
			{
				flag_uart = 0;
				state = SEND1;
			}
			break;

		case SEND1:
		{
			for (uint32_t i = 0; i < NUM_SAMPLES; i++)
			{
				int len = snprintf(buf_tras, sizeof(buf_tras), "%u\r\n", uart_tx_buf[i]);


				if (len > 0)
				{
					HAL_UART_Transmit(&hlpuart1,
							(uint8_t *)buf_tras,
							(uint16_t)len,
							HAL_MAX_DELAY);
				}
			}

			{
				char c[] = ":\r\n";
				HAL_UART_Transmit(&hlpuart1, (uint8_t *)c, strlen(c), HAL_MAX_DELAY);
			}


			for (uint32_t i = 0; i < NUM_SAMPLES; i++)
			{
				int len2 = snprintf(buf_tras2, sizeof(buf_tras), "%u\r\n", uart_tx_buf2[i]);


				if (len2 > 0)
				{
					HAL_UART_Transmit(&hlpuart1,
							(uint8_t *)buf_tras2,
							(uint16_t)len2,
							HAL_MAX_DELAY);
				}
			}

			{
				char c[] = ":\r\n";
				HAL_UART_Transmit(&hlpuart1, (uint8_t *)c, strlen(c), HAL_MAX_DELAY);
			}


			state = WAIT_FETCH2;
			break;
		}
		case WAIT_FETCH2:
			if (flag_uart == 1)
			{
				flag_uart = 0;
				state = SEND2;
			}
			break;

		case SEND2:
		{
			for (uint32_t i = 0; i < NUM_SAMPLES; i++)
			{
				int len = snprintf(buf_tras, sizeof(buf_tras), "%u\r\n", uart_tx_buf[i]);


				if (len > 0)
				{
					HAL_UART_Transmit(&hlpuart1,
							(uint8_t *)buf_tras,
							(uint16_t)len,
							HAL_MAX_DELAY);
				}
			}

			{
				char c[] = ":\r\n";
				HAL_UART_Transmit(&hlpuart1, (uint8_t *)c, strlen(c), HAL_MAX_DELAY);
			}


			for (uint32_t i = 0; i < NUM_SAMPLES; i++)
			{
				int len2 = snprintf(buf_tras2, sizeof(buf_tras), "%u\r\n", uart_tx_buf2[i]);


				if (len2 > 0)
				{
					HAL_UART_Transmit(&hlpuart1,
							(uint8_t *)buf_tras2,
							(uint16_t)len2,
							HAL_MAX_DELAY);
				}
			}

			{
				char c[] = ":\r\n";
				HAL_UART_Transmit(&hlpuart1, (uint8_t *)c, strlen(c), HAL_MAX_DELAY);
			}


			state = IDLE;
			break;
		}


		}
	}
	/* USER CODE END 3 */
}
/* USER CODE BEGIN CLK 1 */
/* USER CODE END CLK 1 */

/**
 * @brief System Clock Configuration
 * @retval None
 */
void SystemClock_Config(void)
{
	RCC_OscInitTypeDef RCC_OscInitStruct = {0};
	RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

	/** Configure the System Power Supply
	 */
	if (HAL_PWREx_ConfigSupply(PWR_EXTERNAL_SOURCE_SUPPLY) != HAL_OK)
	{
		Error_Handler();
	}

	/** Configure the main internal regulator output voltage
	 */
	if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1) != HAL_OK)
	{
		Error_Handler();
	}

	/* Enable HSI */
	RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
	RCC_OscInitStruct.HSIState = RCC_HSI_ON;
	RCC_OscInitStruct.HSIDiv = RCC_HSI_DIV1;
	RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
	RCC_OscInitStruct.PLL1.PLLState = RCC_PLL_NONE;
	RCC_OscInitStruct.PLL2.PLLState = RCC_PLL_NONE;
	RCC_OscInitStruct.PLL3.PLLState = RCC_PLL_NONE;
	RCC_OscInitStruct.PLL4.PLLState = RCC_PLL_NONE;
	if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
	{
		Error_Handler();
	}

	/** Get current CPU/System buses clocks configuration and if necessary switch
 to intermediate HSI clock to ensure target clock can be set
	 */
	HAL_RCC_GetClockConfig(&RCC_ClkInitStruct);
	if ((RCC_ClkInitStruct.CPUCLKSource == RCC_CPUCLKSOURCE_IC1) ||
			(RCC_ClkInitStruct.SYSCLKSource == RCC_SYSCLKSOURCE_IC2_IC6_IC11))
	{
		RCC_ClkInitStruct.ClockType = (RCC_CLOCKTYPE_CPUCLK | RCC_CLOCKTYPE_SYSCLK);
		RCC_ClkInitStruct.CPUCLKSource = RCC_CPUCLKSOURCE_HSI;
		RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
		if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct) != HAL_OK)
		{
			/* Initialization Error */
			Error_Handler();
		}
	}

	/** Initializes the RCC Oscillators according to the specified parameters
	 * in the RCC_OscInitTypeDef structure.
	 */
	RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_NONE;
	RCC_OscInitStruct.PLL1.PLLState = RCC_PLL_ON;
	RCC_OscInitStruct.PLL1.PLLSource = RCC_PLLSOURCE_HSI;
	RCC_OscInitStruct.PLL1.PLLM = 4;
	RCC_OscInitStruct.PLL1.PLLN = 75;
	RCC_OscInitStruct.PLL1.PLLFractional = 0;
	RCC_OscInitStruct.PLL1.PLLP1 = 1;
	RCC_OscInitStruct.PLL1.PLLP2 = 1;
	RCC_OscInitStruct.PLL2.PLLState = RCC_PLL_NONE;
	RCC_OscInitStruct.PLL3.PLLState = RCC_PLL_NONE;
	RCC_OscInitStruct.PLL4.PLLState = RCC_PLL_NONE;
	if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
	{
		Error_Handler();
	}

	/** Initializes the CPU, AHB and APB buses clocks
	 */
	RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_CPUCLK|RCC_CLOCKTYPE_HCLK
			|RCC_CLOCKTYPE_SYSCLK|RCC_CLOCKTYPE_PCLK1
			|RCC_CLOCKTYPE_PCLK2|RCC_CLOCKTYPE_PCLK5
			|RCC_CLOCKTYPE_PCLK4;
	RCC_ClkInitStruct.CPUCLKSource = RCC_CPUCLKSOURCE_IC1;
	RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_IC2_IC6_IC11;
	RCC_ClkInitStruct.AHBCLKDivider = RCC_HCLK_DIV4;
	RCC_ClkInitStruct.APB1CLKDivider = RCC_APB1_DIV1;
	RCC_ClkInitStruct.APB2CLKDivider = RCC_APB2_DIV1;
	RCC_ClkInitStruct.APB4CLKDivider = RCC_APB4_DIV1;
	RCC_ClkInitStruct.APB5CLKDivider = RCC_APB5_DIV1;
	RCC_ClkInitStruct.IC1Selection.ClockSelection = RCC_ICCLKSOURCE_PLL1;
	RCC_ClkInitStruct.IC1Selection.ClockDivider = 2;
	RCC_ClkInitStruct.IC2Selection.ClockSelection = RCC_ICCLKSOURCE_PLL1;
	RCC_ClkInitStruct.IC2Selection.ClockDivider = 3;
	RCC_ClkInitStruct.IC6Selection.ClockSelection = RCC_ICCLKSOURCE_PLL1;
	RCC_ClkInitStruct.IC6Selection.ClockDivider = 2;
	RCC_ClkInitStruct.IC11Selection.ClockSelection = RCC_ICCLKSOURCE_PLL1;
	RCC_ClkInitStruct.IC11Selection.ClockDivider = 3;

	if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct) != HAL_OK)
	{
		Error_Handler();
	}
}

/**
 * @brief ADC1 Initialization Function
 * @param None
 * @retval None
 */
static void MX_ADC1_Init(void)
{

	/* USER CODE BEGIN ADC1_Init 0 */

	/* USER CODE END ADC1_Init 0 */

	ADC_MultiModeTypeDef multimode = {0};
	ADC_ChannelConfTypeDef sConfig = {0};

	/* USER CODE BEGIN ADC1_Init 1 */

	/* USER CODE END ADC1_Init 1 */

	/** Common config
	 */
	hadc1.Instance = ADC1;
	hadc1.Init.Resolution = ADC_RESOLUTION_8B;
	hadc1.Init.GainCompensation = 0;
	hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
	hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
	hadc1.Init.LowPowerAutoWait = DISABLE;
	hadc1.Init.ContinuousConvMode = ENABLE;
	hadc1.Init.NbrOfConversion = 1;
	hadc1.Init.DiscontinuousConvMode = DISABLE;
	hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
	hadc1.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
	hadc1.Init.ConversionDataManagement = ADC_CONVERSIONDATA_DMA_ONESHOT;
	hadc1.Init.Overrun = ADC_OVR_DATA_PRESERVED;
	hadc1.Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
	hadc1.Init.OversamplingMode = DISABLE;
	if (HAL_ADC_Init(&hadc1) != HAL_OK)
	{
		Error_Handler();
	}

	/** Configure the ADC multi-mode : DUAL REGULAR SIMULTANEOUS
	 *  Data format = 32..10-bit (16-bit half-words in CDR). We stay on this
	 *  format even though the resolution is 8-bit, because the dedicated 8-bit
	 *  packed format (DAMDF=0b11) is reported to corrupt the slave half on this
	 *  ADC IP. The 8-bit code lands in the low byte of each 16-bit half-word.
	 */
	multimode.Mode             = ADC_DUALMODE_REGSIMULT;
	multimode.DualModeData     = ADC_DUALMODEDATAFORMAT_32_10_BITS;
	multimode.TwoSamplingDelay = ADC_TWOSAMPLINGDELAY_1CYCLE;
	if (HAL_ADCEx_MultiModeConfigChannel(&hadc1, &multimode) != HAL_OK)
	{
		Error_Handler();
	}


	/** Configure Regular Channel
	 */
	sConfig.Channel = ADC_CHANNEL_5;
	sConfig.Rank = ADC_REGULAR_RANK_1;
	sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
	sConfig.SingleDiff = ADC_SINGLE_ENDED;
	sConfig.OffsetNumber = ADC_OFFSET_NONE;
	sConfig.Offset = 0;
	if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
	{
		Error_Handler();
	}
	/* USER CODE BEGIN ADC1_Init 2 */

	/* USER CODE END ADC1_Init 2 */

}

/**
 * @brief ADC2 Initialization Function
 * @param None
 * @retval None
 */
static void MX_ADC2_Init(void)
{

	/* USER CODE BEGIN ADC2_Init 0 */

	/* USER CODE END ADC2_Init 0 */

	ADC_ChannelConfTypeDef sConfig = {0};

	/* USER CODE BEGIN ADC2_Init 1 */

	/* USER CODE END ADC2_Init 1 */

	/** Common config
	 */
	hadc2.Instance = ADC2;
	hadc2.Init.Resolution = ADC_RESOLUTION_8B;
	hadc2.Init.GainCompensation = 0;
	hadc2.Init.ScanConvMode = ADC_SCAN_DISABLE;
	hadc2.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
	hadc2.Init.LowPowerAutoWait = DISABLE;
	hadc2.Init.ContinuousConvMode = ENABLE;
	hadc2.Init.NbrOfConversion = 1;
	hadc2.Init.DiscontinuousConvMode = DISABLE;
	hadc2.Init.ExternalTrigConv = ADC_SOFTWARE_START;
	hadc2.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
	hadc2.Init.ConversionDataManagement = ADC_CONVERSIONDATA_DMA_ONESHOT;
	hadc2.Init.Overrun = ADC_OVR_DATA_PRESERVED;
	hadc2.Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
	hadc2.Init.OversamplingMode = DISABLE;
	if (HAL_ADC_Init(&hadc2) != HAL_OK)
	{
		Error_Handler();
	}

	/** Configure Regular Channel
	 */
	sConfig.Channel = ADC_CHANNEL_10;
	sConfig.Rank = ADC_REGULAR_RANK_1;
	sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
	sConfig.SingleDiff = ADC_SINGLE_ENDED;
	sConfig.OffsetNumber = ADC_OFFSET_NONE;
	sConfig.Offset = 0;
	if (HAL_ADC_ConfigChannel(&hadc2, &sConfig) != HAL_OK)
	{
		Error_Handler();
	}
	/* USER CODE BEGIN ADC2_Init 2 */

	/* USER CODE END ADC2_Init 2 */

}

/**
 * @brief CACHEAXI Initialization Function
 * @param None
 * @retval None
 */
static void MX_CACHEAXI_Init(void)
{

	/* USER CODE BEGIN CACHEAXI_Init 0 */

	/* USER CODE END CACHEAXI_Init 0 */

	/* USER CODE BEGIN CACHEAXI_Init 1 */

	/* USER CODE END CACHEAXI_Init 1 */
	hcacheaxi.Instance = CACHEAXI;
	if (HAL_CACHEAXI_Init(&hcacheaxi) != HAL_OK)
	{
		Error_Handler();
	}
	/* USER CODE BEGIN CACHEAXI_Init 2 */

	/* USER CODE END CACHEAXI_Init 2 */

}

/**
 * @brief GPDMA1 Initialization Function
 * @param None
 * @retval None
 */
static void MX_GPDMA1_Init(void)
{

	/* USER CODE BEGIN GPDMA1_Init 0 */

	/* USER CODE END GPDMA1_Init 0 */

	/* Peripheral clock enable */
	__HAL_RCC_GPDMA1_CLK_ENABLE();

	/* GPDMA1 interrupt Init */
	HAL_NVIC_SetPriority(GPDMA1_Channel0_IRQn, 0, 0);
	HAL_NVIC_EnableIRQ(GPDMA1_Channel0_IRQn);
	HAL_NVIC_SetPriority(GPDMA1_Channel1_IRQn, 0, 0);
	HAL_NVIC_EnableIRQ(GPDMA1_Channel1_IRQn);

	/* USER CODE BEGIN GPDMA1_Init 1 */

	/* USER CODE END GPDMA1_Init 1 */
	/* USER CODE BEGIN GPDMA1_Init 2 */

	/* USER CODE END GPDMA1_Init 2 */

}

/**
 * @brief LPUART1 Initialization Function
 * @param None
 * @retval None
 */
static void MX_LPUART1_UART_Init(void)
{

	/* USER CODE BEGIN LPUART1_Init 0 */

	/* USER CODE END LPUART1_Init 0 */

	/* USER CODE BEGIN LPUART1_Init 1 */

	/* USER CODE END LPUART1_Init 1 */
	hlpuart1.Instance = LPUART1;
	hlpuart1.Init.BaudRate = 209700;
	hlpuart1.Init.WordLength = UART_WORDLENGTH_8B;
	hlpuart1.Init.StopBits = UART_STOPBITS_1;
	hlpuart1.Init.Parity = UART_PARITY_NONE;
	hlpuart1.Init.Mode = UART_MODE_TX_RX;
	hlpuart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
	hlpuart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
	hlpuart1.Init.ClockPrescaler = UART_PRESCALER_DIV1;
	hlpuart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
	hlpuart1.FifoMode = UART_FIFOMODE_DISABLE;
	if (HAL_UART_Init(&hlpuart1) != HAL_OK)
	{
		Error_Handler();
	}
	if (HAL_UARTEx_SetTxFifoThreshold(&hlpuart1, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
	{
		Error_Handler();
	}
	if (HAL_UARTEx_SetRxFifoThreshold(&hlpuart1, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
	{
		Error_Handler();
	}
	if (HAL_UARTEx_DisableFifoMode(&hlpuart1) != HAL_OK)
	{
		Error_Handler();
	}
	/* USER CODE BEGIN LPUART1_Init 2 */

	/* USER CODE END LPUART1_Init 2 */

}

/**
 * @brief RAMCFG Initialization Function
 * @param None
 * @retval None
 */
static void MX_RAMCFG_Init(void)
{

	/* USER CODE BEGIN RAMCFG_Init 0 */

	/* USER CODE END RAMCFG_Init 0 */

	/* USER CODE BEGIN RAMCFG_Init 1 */

	/* USER CODE END RAMCFG_Init 1 */

	/** Initialize RAMCFG SRAM2
	 */
	hramcfg_SRAM2.Instance = RAMCFG_SRAM2_AXI;
	if (HAL_RAMCFG_Init(&hramcfg_SRAM2) != HAL_OK)
	{
		Error_Handler();
	}
	HAL_RAMCFG_EnableAXISRAM(&hramcfg_SRAM2);

	/** Initialize RAMCFG SRAM3
	 */
	hramcfg_SRAM3.Instance = RAMCFG_SRAM3_AXI;
	if (HAL_RAMCFG_Init(&hramcfg_SRAM3) != HAL_OK)
	{
		Error_Handler();
	}
	HAL_RAMCFG_EnableAXISRAM(&hramcfg_SRAM3);

	/** Initialize RAMCFG SRAM4
	 */
	hramcfg_SRAM4.Instance = RAMCFG_SRAM4_AXI;
	if (HAL_RAMCFG_Init(&hramcfg_SRAM4) != HAL_OK)
	{
		Error_Handler();
	}
	HAL_RAMCFG_EnableAXISRAM(&hramcfg_SRAM4);

	/** Initialize RAMCFG SRAM5
	 */
	hramcfg_SRAM5.Instance = RAMCFG_SRAM5_AXI;
	if (HAL_RAMCFG_Init(&hramcfg_SRAM5) != HAL_OK)
	{
		Error_Handler();
	}
	HAL_RAMCFG_EnableAXISRAM(&hramcfg_SRAM5);

	/** Initialize RAMCFG SRAM6
	 */
	hramcfg_SRAM6.Instance = RAMCFG_SRAM6_AXI;
	if (HAL_RAMCFG_Init(&hramcfg_SRAM6) != HAL_OK)
	{
		Error_Handler();
	}
	HAL_RAMCFG_EnableAXISRAM(&hramcfg_SRAM6);
	/* USER CODE BEGIN RAMCFG_Init 2 */

	/* USER CODE END RAMCFG_Init 2 */

}

/**
 * @brief GPIO Initialization Function
 * @param None
 * @retval None
 */
static void MX_GPIO_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStruct = {0};
	/* USER CODE BEGIN MX_GPIO_Init_1 */

	/* USER CODE END MX_GPIO_Init_1 */

	/* GPIO Ports Clock Enable */
	__HAL_RCC_GPIOC_CLK_ENABLE();
	__HAL_RCC_GPIOE_CLK_ENABLE();
	__HAL_RCC_GPIOH_CLK_ENABLE();
	__HAL_RCC_GPIOD_CLK_ENABLE();
	__HAL_RCC_GPIOB_CLK_ENABLE();
	__HAL_RCC_GPIOA_CLK_ENABLE();
	__HAL_RCC_GPIOG_CLK_ENABLE();

	/*Configure GPIO pin Output Level */
	HAL_GPIO_WritePin(TRIGGER_GPIO_Port, TRIGGER_Pin, GPIO_PIN_RESET);

	/*Configure GPIO pin Output Level */
	HAL_GPIO_WritePin(GPIOG, GREEN_LED_Pin|RED_LED_Pin|BLUE_LED_Pin, GPIO_PIN_RESET);

	/*Configure GPIO pin : I2C1_SDA_Pin */
	GPIO_InitStruct.Pin = I2C1_SDA_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
	GPIO_InitStruct.Alternate = GPIO_AF4_I2C1;
	HAL_GPIO_Init(I2C1_SDA_GPIO_Port, &GPIO_InitStruct);

	/*Configure GPIO pin : I2CA_SCL_Pin */
	GPIO_InitStruct.Pin = I2CA_SCL_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
	GPIO_InitStruct.Alternate = GPIO_AF4_I2C1;
	HAL_GPIO_Init(I2CA_SCL_GPIO_Port, &GPIO_InitStruct);

	/*Configure GPIO pin : TRIGGER_Pin */
	GPIO_InitStruct.Pin = TRIGGER_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
	HAL_GPIO_Init(TRIGGER_GPIO_Port, &GPIO_InitStruct);

	/*Configure GPIO pin : PC13 */
	GPIO_InitStruct.Pin = GPIO_PIN_13;
	GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

	/*Configure GPIO pins : I2C2_SDA_Pin I2C2_SCL_Pin */
	GPIO_InitStruct.Pin = I2C2_SDA_Pin|I2C2_SCL_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
	GPIO_InitStruct.Alternate = GPIO_AF4_I2C2;
	HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

	/*Configure GPIO pins : GREEN_LED_Pin RED_LED_Pin BLUE_LED_Pin */
	GPIO_InitStruct.Pin = GREEN_LED_Pin|RED_LED_Pin|BLUE_LED_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
	HAL_GPIO_Init(GPIOG, &GPIO_InitStruct);

	/*Configure the EXTI line attribute */
	HAL_EXTI_ConfigLineAttributes(EXTI_LINE_13, EXTI_LINE_SEC);

	/* EXTI interrupt init*/
	HAL_NVIC_SetPriority(EXTI13_IRQn, 0, 0);
	HAL_NVIC_EnableIRQ(EXTI13_IRQn);

	/* USER CODE BEGIN MX_GPIO_Init_2 */

	/* USER CODE END MX_GPIO_Init_2 */
}

/**
 * @brief RIF Initialization Function
 * @param None
 * @retval None
 */
static void SystemIsolation_Config(void)
{

	/* USER CODE BEGIN RIF_Init 0 */

	/* USER CODE END RIF_Init 0 */

	/* set all required IPs as secure privileged */
	__HAL_RCC_RIFSC_CLK_ENABLE();

	/* RIF-Aware IPs Config */

	/* set up GPDMA configuration */
	/* set GPDMA1 channel 0 used by ADC2 */
	if (HAL_DMA_ConfigChannelAttributes(&handle_GPDMA1_Channel0,DMA_CHANNEL_SEC|DMA_CHANNEL_PRIV|DMA_CHANNEL_SRC_SEC|DMA_CHANNEL_DEST_SEC)!= HAL_OK )
	{
		Error_Handler();
	}
	/* set GPDMA1 channel 1 used by ADC1 */
	if (HAL_DMA_ConfigChannelAttributes(&handle_GPDMA1_Channel1,DMA_CHANNEL_SEC|DMA_CHANNEL_PRIV|DMA_CHANNEL_SRC_SEC|DMA_CHANNEL_DEST_SEC)!= HAL_OK )
	{
		Error_Handler();
	}

	/* USER CODE BEGIN RIF_Init 1 */

	/* USER CODE END RIF_Init 1 */
	/* USER CODE BEGIN RIF_Init 2 */

	/* USER CODE END RIF_Init 2 */

}

/* USER CODE BEGIN 4 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
	if (huart->Instance == LPUART1)
	{
		if (rx_byte == start_char) {        /* 'A' */
			flag_button = 1;
		}
		else if (rx_byte == FETCH1_char || rx_byte == FETCH2_char) {  /* 'X' or 'Y' */
			flag_uart = 1;
		}
		else if (rx_byte == RESET_char) {
			state = IDLE;
		}
		/* rilancia subito la ricezione */
		HAL_UART_Receive_IT(&hlpuart1, &rx_byte, 1);
	}
}




void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
	/* In dual regular-simultaneous mode the completion is reported on the
	 * MASTER handle (ADC1). ADC2's callback does not fire. */
	if (hadc->Instance == ADC1)
	{
		adc_done2 = 1;
	}

	if (hadc->Instance == ADC2)
	{
		adc_done = 1;
	}
}

void HAL_ADC_ErrorCallback(ADC_HandleTypeDef *hadc)
{
	if (hadc->Instance == ADC1)
	{
		adc_err = HAL_ADC_GetError(hadc);
	}
}

void HAL_DMA_ErrorCallback(DMA_HandleTypeDef *hdma)
{
	if (hdma->Instance == GPDMA1_Channel1)
	{
		dma_err = hdma->ErrorCode;
	}
}

/* USER CODE END 4 */

/**
 * @brief  This function is executed in case of error occurrence.
 * @param None
 * @retval None
 */
void Error_Handler(void)
{
	/* USER CODE BEGIN Error_Handler_Debug */
	/* User can add his own implementation to report the HAL error return state */
	__disable_irq();
	while (1)
	{
	}
	/* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
 * @brief  Reports the name of the source file and the source line number
 *         where the assert_param error has occurred.
 * @param  file: pointer to the source file name
 * @param  line: assert_param error line source number
 * @retval None
 */
void assert_failed(uint8_t *file, uint32_t line)
{
	/* USER CODE BEGIN 6 */
	/* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
	/* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */

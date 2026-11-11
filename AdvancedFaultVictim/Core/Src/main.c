/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file           : main.c
 * @brief          : Main program body
 ******************************************************************************
 * @attention
 *
 * Copyright (c) 2025 STMicroelectronics.
 * All rights reserved.
 *
 * This software is licensed under terms that can be found in the LICENSE file
 * in the root directory of this software component.
 * If no LICENSE file comes with this software, it is provided AS-IS.
 *
 ******************************************************************************
 */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "cmox_crypto.h"
#include "string.h"
#include "stdlib.h"
#include <stdio.h>

/**** FUNZIONAMENTO
 *  Il firmware resta normalmente in IDLE. Quando l'ingresso PA10 (D2) viene portato
 * alto, la board avvia una cifratura AES. Prima dell'encrypt alza un segnale
 * di trigger pA0, attende circa 10 us tramite DWT, esegue la cifratura e poi abbassa
 * il trigger. Al termine invia su UART il messaggio "Encrypt" e torna in IDLE
 * solo quando PA10 torna basso.
 */

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef enum {
	IDLE	= 0x01,
	ENCRYPT	= 0x02,
	WAIT_TRIGGER_LOW=0x03,
	DECRYPT	= 0x04
} VictimState;

typedef enum {
	START_ENCRYPT	= 'E',
	START_DECRYPT	= 'D',
	RESET_STATE		= 'R'
} RXProtocolCMD;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

#define UART_BUFFER_SIZE 	50

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
CRC_HandleTypeDef hcrc;

UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */

/* ===== FSM Vittima */
VictimState v_state 	= IDLE;		// Stato

uint8_t rxByte;         			// Singolo byte ricevuto
char rxBuffer[UART_BUFFER_SIZE]; 	// Buffer per il messaggio ricevuto
uint8_t rxIndex = 0;    			// Indice del buffer

/* ===== Parametri AES ==== */
const uint8_t key[] =
{
		0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, 0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c
};
const uint8_t IV[] =
{
		0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f
};
const uint8_t plaintext[] =
{
		0x6b, 0xc1, 0xbe, 0xe2, 0x2e, 0x40, 0x9f, 0x96, 0xe9, 0x3d, 0x7e, 0x11, 0x73, 0x93, 0x17, 0x2a,
		0xae, 0x2d, 0x8a, 0x57, 0x1e, 0x03, 0xac, 0x9c, 0x9e, 0xb7, 0x6f, 0xac, 0x45, 0xaf, 0x8e, 0x51,
		0x30, 0xc8, 0x1c, 0x46, 0xa3, 0x5c, 0xe4, 0x11, 0xe5, 0xfb, 0xc1, 0x19, 0x1a, 0x0a, 0x52, 0xef,
		0xf6, 0x9f, 0x24, 0x45, 0xdf, 0x4f, 0x9b, 0x17, 0xad, 0x2b, 0x41, 0x7b, 0xe6, 0x6c, 0x37, 0x10
};

const uint8_t expectedCiphertext[] =
{
		0x76, 0x49, 0xab, 0xac, 0x81, 0x19, 0xb2, 0x46, 0xce, 0xe9, 0x8e, 0x9b, 0x12, 0xe9, 0x19, 0x7d,
		0x50, 0x86, 0xcb, 0x9b, 0x50, 0x72, 0x19, 0xee, 0x95, 0xdb, 0x11, 0x3a, 0x91, 0x76, 0x78, 0xb2,
		0x73, 0xbe, 0xd6, 0xb8, 0xe3, 0xc1, 0x74, 0x3b, 0x71, 0x16, 0xe6, 0x9e, 0x22, 0x22, 0x95, 0x16,
		0x3f, 0xf1, 0xca, 0xa1, 0x68, 0x1f, 0xac, 0x09, 0x12, 0x0e, 0xca, 0x30, 0x75, 0x86, 0xe1, 0xa7
};

/* Computed data buffer */
uint8_t computedCiphertext[sizeof(expectedCiphertext)];
uint8_t computedPlaintext[sizeof(plaintext)];

uint32_t last_blink_time = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART2_UART_Init(void);
static void MX_CRC_Init(void);
/* USER CODE BEGIN PFP */

void processRXData(char* data);

cmox_init_retval_t cmox_ll_init(void *pArg)
{
	(void)pArg;
	/* Ensure CRC is enabled for cryptographic processing */
	__HAL_RCC_CRC_RELEASE_RESET();
	__HAL_RCC_CRC_CLK_ENABLE();
	return CMOX_INIT_SUCCESS;
}

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

	/* Reset of all peripherals, Initializes the Flash interface and the Systick. */
	HAL_Init();

	/* USER CODE BEGIN Init */

	/* USER CODE END Init */

	/* Configure the system clock */
	SystemClock_Config();

	/* USER CODE BEGIN SysInit */

	/* USER CODE END SysInit */

	/* Initialize all configured peripherals */
	MX_GPIO_Init();
	MX_USART2_UART_Init();
	MX_CRC_Init();
	/* USER CODE BEGIN 2 */

	/* Abilita il contatore cicli DWT */
	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

	HAL_GPIO_WritePin(TRIGGER_GPIO_Port, TRIGGER_Pin, GPIO_PIN_RESET);

	cmox_init_arg_t init_target = {CMOX_INIT_TARGET_AUTO, NULL};

	/* Initialize cryptographic library */
	if (cmox_initialize(&init_target) != CMOX_INIT_SUCCESS)
	{
		Error_Handler();
	}

	cmox_cipher_retval_t retval;
	size_t computed_size;

	HAL_UART_Receive_IT(&huart2, &rxByte, 1);

	char tx_buffer[50];
	snprintf(tx_buffer, sizeof(tx_buffer), "Start\n");
	HAL_UART_Transmit(&huart2, (uint8_t *)tx_buffer, strlen(tx_buffer), HAL_MAX_DELAY);

	/* USER CODE END 2 */

	/* Infinite loop */
	/* USER CODE BEGIN WHILE */
	while (1)
	{
		/* USER CODE END WHILE */

		/* USER CODE BEGIN 3 */

		switch(v_state) {
		case IDLE:

			if (HAL_GetTick() - last_blink_time >= 500)
			{
				last_blink_time = HAL_GetTick();

				HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);
			}

			if(HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_10)==GPIO_PIN_SET){
				v_state=ENCRYPT;
			}


			//char tx_buffer_me[50];
			//snprintf(tx_buffer_me, sizeof(tx_buffer_me), "Feedback\n");
			//HAL_UART_Transmit(&huart2, (uint8_t *)tx_buffer_me, strlen(tx_buffer_me), HAL_MAX_DELAY);

			break;

		case ENCRYPT:

			// Alza il TRIGGER
			HAL_GPIO_WritePin(TRIGGER_GPIO_Port, TRIGGER_Pin, GPIO_PIN_SET);
			uint32_t start = DWT->CYCCNT;
			uint32_t delay_cycles = 10U * (SystemCoreClock / 1000000U);

			while ((uint32_t)(DWT->CYCCNT - start) < delay_cycles)
			{
			    __NOP();
			}


			HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_SET);

			// Esegui crittografia
			retval = cmox_cipher_encrypt(
					CMOX_AES_CBC_ENC_ALGO,                  /* Use AES CBC algorithm */
					plaintext, sizeof(plaintext),           /* Plaintext to encrypt */
					key, sizeof(key),                       /* AES key to use */
					IV, sizeof(IV),                         /* Initialization vector */
					computedCiphertext, &computed_size	  /* Data buffer to receive generated ciphertext */
			);

			// Abbassa il TRIGGER
			HAL_GPIO_WritePin(TRIGGER_GPIO_Port, TRIGGER_Pin, GPIO_PIN_RESET);
			//HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

			//HAL_Delay(500);

			char tx_buffer[50];
			snprintf(tx_buffer, sizeof(tx_buffer), "Encrypt\n");
			HAL_UART_Transmit(&huart2, (uint8_t *)tx_buffer, strlen(tx_buffer), HAL_MAX_DELAY);

			// Torna in IDLE

			v_state = WAIT_TRIGGER_LOW;

			break;

		case WAIT_TRIGGER_LOW:
			//IN MODO DA NON RIAVVIARE L'ENCRYPT PIù VOLTE NELLA STESSA FINESTRA DI CAMPIONAMNETO
		    if (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_10) == GPIO_PIN_RESET)
		    {
		        v_state = IDLE;
		    }

		    break;

		case DECRYPT:

			char buffer[50];

			// Esegui decrittografia
			retval = cmox_cipher_decrypt(
					CMOX_AES_CBC_DEC_ALGO,                 			/* Use AES CBC algorithm */
					expectedCiphertext, sizeof(expectedCiphertext), 	/* Ciphertext to decrypt */
					key, sizeof(key),                      			/* AES key to use */
					IV, sizeof(IV),                        			/* Initialization vector */
					computedPlaintext, &computed_size					/* Data buffer to receive generated plaintext */
			);

			// Esegui tutti i checks

			uint8_t fail_checks = 0;

			/* Verify API returned value */
			if (retval != CMOX_CIPHER_SUCCESS) {
				snprintf(buffer, sizeof(buffer), "ERROR: LIBRARY ERROR\n");
				fail_checks += 1;
			} else {
				/* Verify generated data size is the expected one */
				if (computed_size != sizeof(plaintext))
				{
					snprintf(buffer, sizeof(buffer), "ERROR: PLAIN WRONG SIZE\n");
					fail_checks += 1;
				}

				/* Verify generated data are the expected ones */
				if (memcmp(plaintext, computedPlaintext, computed_size) != 0)
				{
					snprintf(buffer, sizeof(buffer), "ERROR: DIFFERENT PLAINTEXT\n");
					fail_checks += 1;
				}
			}

			if(fail_checks == 0) {
				snprintf(buffer, sizeof(buffer), "OK: DECRYPT DONE\n");
			}

			HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);

			v_state = IDLE;
			break;
		}
	}
	/* USER CODE END 3 */
}

/**
 * @brief System Clock Configuration
 * @retval None
 */
void SystemClock_Config(void)
{
	RCC_OscInitTypeDef RCC_OscInitStruct = {0};
	RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

	/** Configure the main internal regulator output voltage
	 */
	__HAL_RCC_PWR_CLK_ENABLE();
	__HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

	/** Initializes the RCC Oscillators according to the specified parameters
	 * in the RCC_OscInitTypeDef structure.
	 */
	RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
	RCC_OscInitStruct.HSIState = RCC_HSI_ON;
	RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
	RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
	RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
	RCC_OscInitStruct.PLL.PLLM = 16;
	RCC_OscInitStruct.PLL.PLLN = 336;
	RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
	RCC_OscInitStruct.PLL.PLLQ = 7;
	if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
	{
		Error_Handler();
	}

	/** Initializes the CPU, AHB and APB buses clocks
	 */
	RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
			|RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
	RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
	RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
	RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
	RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

	if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
	{
		Error_Handler();
	}
}

/**
 * @brief CRC Initialization Function
 * @param None
 * @retval None
 */
static void MX_CRC_Init(void)
{

	/* USER CODE BEGIN CRC_Init 0 */

	/* USER CODE END CRC_Init 0 */

	/* USER CODE BEGIN CRC_Init 1 */

	/* USER CODE END CRC_Init 1 */
	hcrc.Instance = CRC;
	if (HAL_CRC_Init(&hcrc) != HAL_OK)
	{
		Error_Handler();
	}
	/* USER CODE BEGIN CRC_Init 2 */

	/* USER CODE END CRC_Init 2 */

}

/**
 * @brief USART2 Initialization Function
 * @param None
 * @retval None
 */
static void MX_USART2_UART_Init(void)
{

	/* USER CODE BEGIN USART2_Init 0 */

	/* USER CODE END USART2_Init 0 */

	/* USER CODE BEGIN USART2_Init 1 */

	/* USER CODE END USART2_Init 1 */
	huart2.Instance = USART2;
	huart2.Init.BaudRate = 115200;
	huart2.Init.WordLength = UART_WORDLENGTH_8B;
	huart2.Init.StopBits = UART_STOPBITS_1;
	huart2.Init.Parity = UART_PARITY_NONE;
	huart2.Init.Mode = UART_MODE_TX_RX;
	huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
	huart2.Init.OverSampling = UART_OVERSAMPLING_16;
	if (HAL_UART_Init(&huart2) != HAL_OK)
	{
		Error_Handler();
	}
	/* USER CODE BEGIN USART2_Init 2 */

	/* USER CODE END USART2_Init 2 */

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
	__HAL_RCC_GPIOH_CLK_ENABLE();
	__HAL_RCC_GPIOA_CLK_ENABLE();
	__HAL_RCC_GPIOB_CLK_ENABLE();

	/*Configure GPIO pin Output Level */
	HAL_GPIO_WritePin(GPIOA, TRIGGER_Pin|LD2_Pin, GPIO_PIN_RESET);

	/*Configure GPIO pin : B1_Pin */
	GPIO_InitStruct.Pin = B1_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

	/*Configure GPIO pins : TRIGGER_Pin LD2_Pin */
	GPIO_InitStruct.Pin = TRIGGER_Pin|LD2_Pin;
	GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
	HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

	/*Configure GPIO pin : PA10 */
	GPIO_InitStruct.Pin = GPIO_PIN_10;
	GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
	GPIO_InitStruct.Pull = GPIO_NOPULL;
	HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

	/* USER CODE BEGIN MX_GPIO_Init_2 */

	/* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
void processRXData(char* data) {
	char buffer[50];

	if(v_state != IDLE) {
		snprintf(buffer, sizeof(buffer), "ERROR: NOT IDLE STATE\n");
		HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);
		return;
	};


	RXProtocolCMD cmd = (RXProtocolCMD)data[0];

	switch(cmd) {

	// E:;
	case START_ENCRYPT:
		if(v_state == IDLE) {
			snprintf(buffer, sizeof(buffer), "OK: ENCRYPT\n");
			v_state = ENCRYPT;
		} else {
			snprintf(buffer, sizeof(buffer), "ERROR: WRONG STATE\n");
		}

		HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);
		break;

		// D:;
	case START_DECRYPT:
		if(v_state == IDLE) {
			v_state = DECRYPT;
			// Non trasmetto risposta perchè aspetto la decrypt ed il check
		} else {
			snprintf(buffer, sizeof(buffer), "ERROR: WRONG STATE\n");
			HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);
		}

		break;

		// R:;
	case RESET_STATE:
		snprintf(buffer, sizeof(buffer), "OK: IDLE STATE\n");
		HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);

		v_state = IDLE;
		break;
	}



}
/*
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
	if(huart->Instance == USART2) {
		if (rxByte == ';') {
			rxBuffer[rxIndex] = '\0';
			processRXData(rxBuffer);
			rxIndex = 0;
		} else {
			if (rxIndex < UART_BUFFER_SIZE - 1) {
				rxBuffer[rxIndex++] = rxByte;
			}
		}

		HAL_UART_Receive_IT(&huart2, &rxByte, 1);
	}
}*/
/* USER CODE END 4 */

/**
 * @brief  This function is executed in case of error occurrence.
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

#ifdef  USE_FULL_ASSERT
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

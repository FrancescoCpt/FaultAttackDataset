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
#include "string.h"
#include "stdlib.h"
#include <stdio.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

typedef enum {
	IDLE	= 0x01,
	ARMED	= 0x02,
	ATTACK	= 0x03
} AttackerState;

typedef enum {
	GLITCH_COUNT	= 'C',
	GLITCH_LATENCY	= 'L',
	GLITCH_PERIOD 	= 'P',
	GLITCH_WIDTH	= 'W',
	RESET_STATE		= 'R',
	ARMED_SWITCH	= 'A'
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
TIM_HandleTypeDef htim2;

UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */
AttackerState a_state 	= IDLE;		// Stato

uint8_t rxByte;         			// Singolo byte ricevuto
char rxBuffer[UART_BUFFER_SIZE]; 	// Buffer per il messaggio ricevuto
uint8_t rxIndex = 0;    			// Indice del buffer

int glitch_count = 1;
int glitch_period = 200;
int glitch_width = 10;
int glitch_latency = 0;

int wait_latency_end = 0;
int wait_glitch_end = 0;
int trigger_event = 0;


/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART2_UART_Init(void);
static void MX_TIM2_Init(void);
/* USER CODE BEGIN PFP */
void processRXData(char* data);
int getParam(uint8_t index, char* origin_str, char* param);
void DWT_Init(void);
void delay_us(uint32_t us);
void delay_ns(uint32_t ns);
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
  MX_TIM2_Init();
  /* USER CODE BEGIN 2 */
  DWT_Init();
  HAL_UART_Receive_IT(&huart2, &rxByte, 1);

  //__HAL_TIM_MOE_ENABLE(&htim1);
  //HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
  //HAL_TIM_OnePulse_Start(&htim2, TIM_CHANNEL_1);


  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

	  switch(a_state) {
	  case IDLE:
		  HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		  HAL_Delay(500);
		  HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		  HAL_Delay(500);
		  break;

	  case ARMED:
		  MX_TIM2_Init();

		  TIM2->CCR1 = glitch_width;

		  //HAL_TIM_OnePulse_Start(&htim1, TIM_CHANNEL_1);
		  //MX_TIM2_Init();
		  //MX_TIM1_Init();

		  //HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
		  //HAL_TIM_OnePulse_Start(&htim1, TIM_CHANNEL_1);
		  // Prepara i timer per il treno di glitch
		  //HAL_TIM_OnePulse_Stop(&htim1, TIM_CHANNEL_1);
		  //__HAL_TIM_DISABLE(&htim1);
		  //__HAL_TIM_DISABLE(&htim2);

		  // Set glitch number
		  //htim1.Instance->RCR = glitch_count-1;

		  // Set glitch period
		  //__HAL_TIM_SET_AUTORELOAD(&htim1, glitch_period);
		  //__HAL_TIM_SET_AUTORELOAD(&htim2, glitch_period-1);

		  // Set glitch width
		  //int real_glitch_width = glitch_period-glitch_width;
		  //__HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, real_glitch_width);

		  // Reset TIM1
		  //__HAL_TIM_SET_COUNTER(&htim1, 0);

		  // Forzo aggiornamento immediato
		  //htim1.Instance->EGR = TIM_EGR_UG;
		  //htim2.Instance->EGR = TIM_EGR_UG;

		  //HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_SET);

		  // Aspettiamo il trigger
		  //while (trigger_event == 0) { }
		  //trigger_event = 0;

		  // Attacco
		  //HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
		  //HAL_TIM_OnePulse_Start(&htim2, TIM_CHANNEL_1);

		  //HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

		  // Aspetta 1 s (cuscino di sicurezza) per far terminare i timer
		  //HAL_Delay(3000);
		  //HAL_TIMEx_PWMN_Stop(&htim1, TIM_CHANNEL_1);

		  //char buffer[50];
		  //snprintf(buffer, sizeof(buffer), "OK: ATTACK ENDED\n");
		  //HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);

		  if(trigger_event>0) {
			  //HAL_TIM_OnePulse_Start(&htim2, TIM_CHANNEL_1);
			  HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_1);
			  a_state = ATTACK;
		  }
		  break;

	  case ATTACK:
		  trigger_event = 0;
		  a_state = IDLE;
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
  * @brief TIM2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM2_Init(void)
{

  /* USER CODE BEGIN TIM2_Init 0 */

  /* USER CODE END TIM2_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  /* USER CODE BEGIN TIM2_Init 1 */

  /* USER CODE END TIM2_Init 1 */
  htim2.Instance = TIM2;
  htim2.Init.Prescaler = 0;
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = glitch_period;
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim2, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_OnePulse_Init(&htim2, TIM_OPMODE_SINGLE) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM2;
  sConfigOC.Pulse = glitch_width;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM2_Init 2 */

  /* USER CODE END TIM2_Init 2 */
  HAL_TIM_MspPostInit(&htim2);

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
  HAL_GPIO_WritePin(GPIOA, REFERENCE_Pin|LD2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin : B1_Pin */
  GPIO_InitStruct.Pin = B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : TRIGGER_Pin */
  GPIO_InitStruct.Pin = TRIGGER_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(TRIGGER_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : REFERENCE_Pin */
  GPIO_InitStruct.Pin = REFERENCE_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(REFERENCE_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : LD2_Pin */
  GPIO_InitStruct.Pin = LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);

  /* EXTI interrupt init*/
  HAL_NVIC_SetPriority(EXTI1_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(EXTI1_IRQn);

  HAL_NVIC_SetPriority(EXTI15_10_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);

  /* USER CODE BEGIN MX_GPIO_Init_2 */
  GPIO_InitStruct.Pin = GPIO_PIN_0;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
int getParam(uint8_t index, char* origin_str, char* param) {
	int origin_start_idx = 0;
	int origin_end_idx = 0;
	int i = 0;
	int control_find = 0;

	while(origin_start_idx == 0 || origin_end_idx == 0) {
		if(origin_str[i] == ':') {
			if(origin_start_idx == 0) {
				control_find += 1;
				if(control_find == index) {
					origin_start_idx = i + 1;
				}
			} else {
				origin_end_idx = i;
			}
		} else if (origin_str[i] == '\0') {
			origin_start_idx = -1;
			origin_end_idx = -1;
		}

		i++;
	}

	if(origin_start_idx > 0 && origin_end_idx > 0) {
		int len = origin_end_idx - origin_start_idx;
		memcpy(param, &origin_str[origin_start_idx], len);
		param[len] = '\0';
		return len;
	} else {
		return -1;
	}
}

void processRXData(char* data) {
	char buffer[50];

	RXProtocolCMD cmd = (RXProtocolCMD)data[0];

	if(a_state != IDLE && cmd != RESET_STATE) {
		snprintf(buffer, sizeof(buffer), "ERROR: NOT IDLE STATE\n");
		HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);
		return;
	};

	switch(cmd) {

	// C:<COUNT>:;
	case GLITCH_COUNT:
		char count_str[5];
		getParam(1, data, count_str);
		glitch_count = atoi(count_str);
		snprintf(buffer, sizeof(buffer), "OK: G_COUNT=%d\n", glitch_count);
		break;

	// L:<LATENCY>:;
	case GLITCH_LATENCY:
		char latency_str[5];
		getParam(1, data, latency_str);
		glitch_latency = atoi(latency_str);
		snprintf(buffer, sizeof(buffer), "OK: G_LATENCY=%d\n", glitch_latency);
		break;

	// W:<WIDTH>:;
	case GLITCH_WIDTH:
		char width_str[5];
		getParam(1, data, width_str);
		glitch_width = atoi(width_str);
		snprintf(buffer, sizeof(buffer), "OK: G_WIDTH=%d\n", glitch_width);
		break;

	// P:<PERIOD>:;
	case GLITCH_PERIOD:
		char period_str[5];
		getParam(1, data, period_str);
		glitch_period = atoi(period_str);
		snprintf(buffer, sizeof(buffer), "OK: G_PERIOD=%d\n", glitch_period);
		break;

	// R:;
	case RESET_STATE:
		glitch_count = 1;
		glitch_latency = 0;
		glitch_period = 200;
		glitch_width = 10;

		a_state = IDLE;
		snprintf(buffer, sizeof(buffer), "OK: IDLE\n");
		break;

	case ARMED_SWITCH:
		a_state = ARMED;
		snprintf(buffer, sizeof(buffer), "OK: ARMED\n");
		break;
	}


	HAL_UART_Transmit(&huart2, (uint8_t *)buffer, strlen(buffer), HAL_MAX_DELAY);
}

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
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin) {
	if(GPIO_Pin == TRIGGER_Pin) {
		if(a_state == ARMED) {
			trigger_event = 1;
		}

		if(a_state == ATTACK) {
			trigger_event = 1;
		}
	}
}

void DWT_Init(void)
{
    // Abilita TRC
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    // Reset del counter
    DWT->CYCCNT = 0;
    // Abilita il counter
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

void delay_us(uint32_t us)
{
    uint32_t cycles = (SystemCoreClock / 1000000) * us;
    uint32_t start = DWT->CYCCNT;
    while ((DWT->CYCCNT - start) < cycles);
}

void delay_ns(uint32_t ns)
{
    uint32_t cycles = (SystemCoreClock / 1000000000) * ns;
    uint32_t start = DWT->CYCCNT;
    while ((DWT->CYCCNT - start) < cycles);
}

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

/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
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
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>

/****FUNZIONAMENTO***
 * Il firmware riceve il comando 'A' tramite uart, arma TIM1 in One Pulse Mode:
 * il timer resta in attesa di un trigger esterno su TIM1_CH2/TI2FP2 (PA9/D8). Al
 * trigger, viene generato un impulso PWM di attacco su TIM1_CH1 (PA8 / D7) con
 * TON = 1us e ritardo casuale impostato tramite CCR1.
 * Il valore di CCR1 è generato in modo randomico, salvato e
 * inviato al PC con il comando 'I'. Dopo l'invio viene preparato il CCR1
 * per l'attacco successivo.
 *
 */

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
TIM_HandleTypeDef htim1;

UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */
static uint8_t rxByte;

static volatile uint8_t system_armed = 0;

static volatile uint32_t last_pwm_ccr1 = 0;
static volatile uint8_t last_pwm_valid = 0;

static volatile uint32_t armed_ccr1 = 0;

static uint32_t next_ccr1 = 5687U;
static volatile uint8_t next_ccr1_ready = 0;

static uint32_t rng_state = 0x12345678U;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_TIM1_Init(void);
static void MX_USART2_UART_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void PA8_As_GPIO_Low(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    __HAL_RCC_GPIOA_CLK_ENABLE();

    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_8, GPIO_PIN_RESET);

    GPIO_InitStruct.Pin = GPIO_PIN_8;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_PULLDOWN;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_8, GPIO_PIN_RESET);
}

static void PA8_As_TIM1_CH1(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_8;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull = GPIO_PULLDOWN;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF1_TIM1;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
}
static void Arm_TIM1_OnePulse(void)
{
    PA8_As_TIM1_CH1();

    CLEAR_BIT(htim1.Instance->CR1, TIM_CR1_CEN);

    CLEAR_BIT(htim1.Instance->SMCR, TIM_SMCR_SMS);

    __HAL_TIM_SET_COUNTER(&htim1, 0);

    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_UPDATE);
    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_CC1);
    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_CC2);
    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_TRIGGER);

    TIM_CCxChannelCmd(htim1.Instance, TIM_CHANNEL_1, TIM_CCx_ENABLE);

    __HAL_TIM_MOE_ENABLE(&htim1);

    __HAL_TIM_ENABLE_IT(&htim1, TIM_IT_UPDATE);

    /* Enable trigger last: a subsequent CEN clear could cancel a real edge. */
    MODIFY_REG(htim1.Instance->SMCR, TIM_SMCR_SMS, TIM_SLAVEMODE_TRIGGER);
}

static void Disarm_TIM1_Trigger(void)
{
    CLEAR_BIT(htim1.Instance->CR1, TIM_CR1_CEN);

    CLEAR_BIT(htim1.Instance->SMCR, TIM_SMCR_SMS);

    __HAL_TIM_DISABLE_IT(&htim1, TIM_IT_UPDATE);

    TIM_CCxChannelCmd(htim1.Instance, TIM_CHANNEL_1, TIM_CCx_DISABLE);

    __HAL_TIM_MOE_DISABLE(&htim1);

    __HAL_TIM_SET_COUNTER(&htim1, 0);

    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_UPDATE);
    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_CC1);
    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_CC2);
    __HAL_TIM_CLEAR_FLAG(&htim1, TIM_FLAG_TRIGGER);

    PA8_As_GPIO_Low();

    system_armed = 0;
}

static uint32_t TIM1_GetClockHz(void)
{
    uint32_t pclk2 = HAL_RCC_GetPCLK2Freq();

    /*
     * Su STM32F4, se APB2 prescaler è diverso da 1,
     * il clock dei timer su APB2 è 2 * PCLK2.
     */
    if ((RCC->CFGR & RCC_CFGR_PPRE2) == 0U)
    {
        return pclk2;
    }
    else
    {
        return 2U * pclk2;
    }
}

static uint32_t RNG_U32(void)
{
    rng_state = (1664525U * rng_state) + 1013904223U;
    return rng_state;
}

static uint16_t RNG_U16(void)
{
    return (uint16_t)(RNG_U32() >> 16);
}

static int32_t Random_Normal_Ticks(int32_t std_ticks)
{
    int32_t sum = 0;

    for (uint8_t i = 0; i < 12; i++)
    {
        sum += (int32_t)RNG_U16();
    }

    int32_t centered = sum - (12 * 32768);

    return (int32_t)(((int64_t)centered * std_ticks) / 65536);
}

static uint32_t Generate_Random_CCR1(void)
{
    uint32_t tim_clk_hz = TIM1_GetClockHz();

    const int32_t mean_ccr_ticks = (int32_t)((tim_clk_hz * 44.058) / 1000000U);

    /*
     * 3.58 us di deviazione standard.
     * Con TIM1 = 84 MHz -> 3.58052us = 300.76368 tick.
     */
    int32_t std_ccr_ticks = (int32_t)((tim_clk_hz * 3.58052) / 1000000U);

    int32_t ccr = mean_ccr_ticks + Random_Normal_Ticks(std_ccr_ticks);

    uint32_t ton_ticks = tim_clk_hz / 1000000U;   // 1 us

    if (ccr < 1)
    {
        ccr = 1;
    }

    if ((uint32_t)ccr > (0xFFFFU - ton_ticks))
    {
        ccr = (int32_t)(0xFFFFU - ton_ticks);
    }

    return (uint32_t)ccr;
}

static void TIM1_Config_OnePulse_WithCCR(uint32_t ccr_ticks)
{
    uint32_t tim_clk_hz = TIM1_GetClockHz();

    uint32_t ton_ticks = tim_clk_hz / 1000000U;   // TON = 1 us

    uint32_t arr_ticks = ccr_ticks + ton_ticks - 1U;

    CLEAR_BIT(htim1.Instance->CR1, TIM_CR1_CEN);

    __HAL_TIM_SET_PRESCALER(&htim1, 0);
    __HAL_TIM_SET_AUTORELOAD(&htim1, arr_ticks);
    __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_1, ccr_ticks);
    __HAL_TIM_SET_COUNTER(&htim1, 0);

    htim1.Instance->EGR = TIM_EGR_UG;

    __HAL_TIM_CLEAR_FLAG(&htim1,
                         TIM_FLAG_UPDATE |
                         TIM_FLAG_CC1 |
                         TIM_FLAG_CC2 |
                         TIM_FLAG_TRIGGER);
}

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
	// ATTACKER
	HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_TIM1_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */


  rng_state ^= HAL_GetTick();

  next_ccr1 = Generate_Random_CCR1();
  next_ccr1_ready = 1;

  PA8_As_GPIO_Low();

  HAL_UART_Receive_IT(&huart2, &rxByte, 1);


  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
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
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

  /* USER CODE BEGIN TIM1_Init 0 */

  /* USER CODE END TIM1_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */

  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  htim1.Init.Prescaler = 0;
  htim1.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim1.Init.Period = 84;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 0;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim1, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_OnePulse_Init(&htim1, TIM_OPMODE_SINGLE) != HAL_OK)
  {
    Error_Handler();
  }
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_TRIGGER;
  sSlaveConfig.InputTrigger = TIM_TS_TI2FP2;
  sSlaveConfig.TriggerPolarity = TIM_TRIGGERPOLARITY_RISING;
  sSlaveConfig.TriggerFilter = 0;
  if (HAL_TIM_SlaveConfigSynchro(&htim1, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM2;
  sConfigOC.Pulse = 1;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 0;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM1_Init 2 */
  /* SlaveConfigSynchro sets polarity/filter, but does not select CC2S. */
  TIM_IC_InitTypeDef trigger_input = {0};
  trigger_input.ICPolarity = TIM_ICPOLARITY_RISING;
  trigger_input.ICSelection = TIM_ICSELECTION_DIRECTTI;
  trigger_input.ICPrescaler = TIM_ICPSC_DIV1;
  trigger_input.ICFilter = 0;
  if (HAL_TIM_IC_ConfigChannel(&htim1, &trigger_input, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }

  /* Accept external triggers only after the UART arm command. */
  CLEAR_BIT(htim1.Instance->SMCR, TIM_SMCR_SMS);
  CLEAR_BIT(htim1.Instance->CR1, TIM_CR1_CEN);


  /* USER CODE END TIM1_Init 2 */
  HAL_TIM_MspPostInit(&htim1);

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
  HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin : B1_Pin */
  GPIO_InitStruct.Pin = B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : LD2_Pin */
  GPIO_InitStruct.Pin = LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2)
    {
        if (rxByte == 'A')
        {
            if ((system_armed == 0) && (next_ccr1_ready))
            {
                armed_ccr1 = next_ccr1;
                next_ccr1_ready = 0;

                TIM1_Config_OnePulse_WithCCR(armed_ccr1);

                system_armed = 1;
                Arm_TIM1_OnePulse();
            }
        }
        else if (rxByte == 'I')
        {
            char msg[64];
            int len;

            if (last_pwm_valid)
            {
                len = snprintf(msg,
                               sizeof(msg),
                               "CCR1=%lu\r\n",
                               (unsigned long)last_pwm_ccr1);
            }
            else
            {
                len = snprintf(msg,
                               sizeof(msg),
                               "NO_PWM\r\n");
            }

            HAL_UART_Transmit(&huart2,
                              (uint8_t *)msg,
                              (uint16_t)len,
                              100);

            /*
             * Preparo già il CCR1 del prossimo attacco.
             * Questo avviene DOPO aver inviato il valore al PC,
             * per evitare un peggioramento della latenza.
             */
            rng_state ^= HAL_GetTick();
            next_ccr1 = Generate_Random_CCR1();
            next_ccr1_ready = 1;
        }

        HAL_UART_Receive_IT(&huart2, &rxByte, 1);
    }
}

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
    if (htim->Instance == TIM1)
    {
        last_pwm_ccr1 = armed_ccr1;
        last_pwm_valid = 1;

        Disarm_TIM1_Trigger();
    }
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

# Service: <Name>

## 1. Responsibility
Короткий опис єдиної відповідальності сервісу (1-2 речення).

## 2. File Structure
Точна фактична структура (якщо вже існує) з коментарями.

## 3. Environment Variables
| Variable | Required | Default | Description |

> **Конвенція (обов'язково):** якщо сервіс залежить від Azure-ресурсів, НЕ дублюй `AZURE_*` змінні тут. `containers/azure.md` — єдине джерело правди для всіх Azure-змінних (Service Principal автентифікація + ендпоінти ресурсів). Замість таблиці змінних — вкажи, від яких саме Azure-ресурсів залежить сервіс, і дай посилання на `azure.md` § 3. Дивись `head.md` § 3 як приклад.

## 4. Inbound Communication
Що сервіс приймає: звідки, у якому форматі, з якою періодичністю/тригером.
| Source | Channel | Format | Trigger |

## 5. Outbound Communication
Що сервіс надсилає: куди, формат, тригер.
| Destination | Channel | Format | Trigger |

## 6. Internal Logic / State Machine
Детальний опис роботи. Якщо є стейт-машина (Head: Leader/Follower/Updating) — діаграма станів і переходів.

## 7. Logging
- Що логується (рівні: DEBUG/INFO/WARNING/ERROR)
- Куди йдуть логи (локальний файл / Azure Blob / stdout для docker logs)
- Формат запису (structured JSON чи plain text)
- Чутливі дані які НЕ можна логувати (API keys, user content)

## 8. Metrics
- Які метрики збираються (CPU/RAM вже є; чи є сервіс-специфічні — наприклад, "tasks_in_queue" для ai_worker)
- Як часто збираються
- Куди відправляються (Azure Table Storage напряму, чи через Head)

## 9. Failure Modes
Що відбувається коли сервіс падає, втрачає з'єднання, отримує невалідні дані. Це найважливіша секція яку часто пропускають.
| Failure | Detection | Recovery |

## 10. Dependencies
Які інші сервіси/Azure ресурси потрібні для роботи. Що відбувається якщо залежність недоступна.

## 11. Health Check
Як перевірити що сервіс живий і функціонує (не просто "процес запущений", а "процес реально працює").

## 12. Versioning & Update Behavior
Як сервіс реагує на сигнал оновлення (для Head/Bot/AI Worker — специфічна поведінка graceful shutdown).
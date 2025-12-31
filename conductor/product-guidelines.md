# Product Guidelines - Squid

## Tone and Voice
- **Professional and Technical:** Use clear and precise terminology appropriate for scientists, engineers, and researchers. Avoid unnecessary jargon where simple language suffices, but prioritize accuracy.

## Design Principles
- **High-Performance Responsiveness:** The interface must provide low-latency feedback, especially during manual control and live imaging, to ensure a seamless user experience.
- **Modular and Customizable:** UI elements should be organized logically and, where possible, allow for customization to suit different imaging tasks and user preferences.

## Reliability and Error Handling
- **Comprehensive Logging:** Maintain detailed logs of hardware states, software events, and errors. This is critical for troubleshooting complex hardware-software interactions and ensuring the reproducibility of scientific data.

## Hardware-Software Interaction
- **Robust State Management:** The software must reliably track and manage the state of all connected hardware components, ensuring consistency even after restarts or unexpected disconnections.
- **Explicit Hardware Abstraction:** Device-specific logic should be abstracted into clear interfaces, making it easier to support new hardware or swap existing components.
- **Performance-Optimized Communication:** Use efficient communication protocols to achieve the tight synchronization required for advanced acquisition sequences.

use crate::bridge_contract::BridgeHostContext;

use vst3::Steinberg::Vst::{ProcessContext, ProcessContext_};

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BridgeTransportState {
    pub is_playing: bool,
    pub tempo_bpm: f64,
    pub meter_numerator: i32,
    pub meter_denominator: i32,
}

impl Default for BridgeTransportState {
    fn default() -> Self {
        Self {
            is_playing: false,
            tempo_bpm: 120.0,
            meter_numerator: 4,
            meter_denominator: 4,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct BridgeProcessorState {
    sample_rate: f64,
    block_size: usize,
    transport: BridgeTransportState,
    host_context: Option<BridgeHostContext>,
}

impl BridgeProcessorState {
    pub fn prepare(&mut self, sample_rate: f64, block_size: usize) {
        self.sample_rate = sample_rate.max(1.0);
        self.block_size = block_size.max(1);
    }

    pub fn set_transport(
        &mut self,
        is_playing: bool,
        tempo_bpm: f64,
        meter_numerator: i32,
        meter_denominator: i32,
    ) {
        self.transport = BridgeTransportState {
            is_playing,
            tempo_bpm: tempo_bpm.max(1.0),
            meter_numerator: meter_numerator.max(1),
            meter_denominator: meter_denominator.max(1),
        };
    }

    pub fn apply_process_context(&mut self, context: &ProcessContext) {
        if context.sampleRate > 0.0 {
            self.sample_rate = context.sampleRate;
        }

        let is_playing = context.state & (ProcessContext_::StatesAndFlags_::kPlaying as u32) != 0;
        let tempo_bpm = has_context_flag(context, ProcessContext_::StatesAndFlags_::kTempoValid)
            .then_some(context.tempo.max(1.0));
        let time_signature =
            has_context_flag(context, ProcessContext_::StatesAndFlags_::kTimeSigValid).then_some([
                context.timeSigNumerator.max(1),
                context.timeSigDenominator.max(1),
            ]);
        let project_time_beats = has_context_flag(
            context,
            ProcessContext_::StatesAndFlags_::kProjectTimeMusicValid,
        )
        .then_some(context.projectTimeMusic.max(0.0));
        let bar_position_beats =
            has_context_flag(context, ProcessContext_::StatesAndFlags_::kBarPositionValid)
                .then_some(context.barPositionMusic.max(0.0));
        let loop_range_beats =
            has_context_flag(context, ProcessContext_::StatesAndFlags_::kCycleValid)
                .then_some([
                    context.cycleStartMusic.max(0.0),
                    context.cycleEndMusic.max(0.0),
                ])
                .filter(|[start, end]| end > start);
        let loop_active = has_context_flag(context, ProcessContext_::StatesAndFlags_::kCycleValid)
            .then_some(
                context.state & (ProcessContext_::StatesAndFlags_::kCycleActive as u32) != 0,
            );

        self.transport = BridgeTransportState {
            is_playing,
            tempo_bpm: tempo_bpm.unwrap_or(self.transport.tempo_bpm),
            meter_numerator: time_signature
                .map(|signature| signature[0])
                .unwrap_or(self.transport.meter_numerator),
            meter_denominator: time_signature
                .map(|signature| signature[1])
                .unwrap_or(self.transport.meter_denominator),
        };
        self.host_context = Some(BridgeHostContext {
            sample_rate: Some(self.sample_rate),
            block_size: Some(self.block_size),
            is_playing: Some(is_playing),
            tempo_bpm,
            time_signature,
            project_time_beats,
            bar_position_beats,
            loop_active,
            loop_range_beats,
            selection: None,
        });
    }

    pub fn sample_rate(&self) -> f64 {
        self.sample_rate
    }

    pub fn block_size(&self) -> usize {
        self.block_size
    }

    pub fn transport(&self) -> BridgeTransportState {
        self.transport
    }

    pub fn host_context(&self) -> Option<BridgeHostContext> {
        self.host_context.clone()
    }

    pub fn can_perform_dashboard_io(&self) -> bool {
        false
    }
}

impl Default for BridgeProcessorState {
    fn default() -> Self {
        Self {
            sample_rate: 48_000.0,
            block_size: 256,
            transport: BridgeTransportState::default(),
            host_context: None,
        }
    }
}

fn has_context_flag(context: &ProcessContext, flag: ProcessContext_::StatesAndFlags) -> bool {
    context.state & (flag as u32) != 0
}

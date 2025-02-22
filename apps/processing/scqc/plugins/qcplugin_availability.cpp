/***************************************************************************
 * Copyright (C) GFZ Potsdam                                               *
 * All rights reserved.                                                    *
 *                                                                         *
 * GNU Affero General Public License Usage                                 *
 * This file may be used under the terms of the GNU Affero                 *
 * Public License version 3.0 as published by the Free Software Foundation *
 * and appearing in the file LICENSE included in the packaging of this     *
 * file. Please review the following information to ensure the GNU Affero  *
 * Public License version 3.0 requirements will be met:                    *
 * https://www.gnu.org/licenses/agpl-3.0.html.                             *
 ***************************************************************************/


#define SEISCOMP_COMPONENT SCQC
#include <seiscomp/logging/log.h>

#include <seiscomp/datamodel/waveformquality.h>
#include <seiscomp/plugins/qc/qcbuffer.h>
#include <seiscomp/plugins/qc/qcconfig.h>
#include <seiscomp/qc/qcprocessor_availability.h>
#include "qcplugin_availability.h"


// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
namespace Seiscomp {


namespace Private {

int round(double val) {
	return static_cast<int>(val + 0.5);
}


}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
namespace Applications {
namespace Qc {
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
using namespace Seiscomp::Processing;
using namespace Seiscomp::DataModel;
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
#define REGISTERED_NAME "QcAvailability"

IMPLEMENT_SC_CLASS_DERIVED(QcPluginAvailability, QcPlugin, "QcPluginAvailability");
ADD_SC_PLUGIN("Qc Parameter Availability", "GFZ Potsdam <seiscomp-devel@gfz-potsdam.de>", 0, 1, 0)
REGISTER_QCPLUGIN(QcPluginAvailability, REGISTERED_NAME);
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
QcPluginAvailability::QcPluginAvailability(): QcPlugin() {
	_qcProcessor = new QcProcessorAvailability();
	_qcProcessor->subscribe(this);

	_name = REGISTERED_NAME;
	_parameterNames.push_back("availability");
	_parameterNames.push_back("gaps count");
	_parameterNames.push_back("overlaps count");
}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
void QcPluginAvailability::timeoutTask() {
	if (_qcBuffer->empty()) {
		SEISCOMP_DEBUG("qcAvailability: Waveform buffer is empty");
		return;
	}

	QcParameter* qcp = new QcParameter();
	qcp->recordSamplingFrequency = -1;
	qcp->recordEndTime = Core::Time::UTC();

	// origin of previous buffer item was a real record
	if (_qcBuffer->back()->recordSamplingFrequency != -1) {
		_lastRecordEndTime = _qcBuffer->back()->recordEndTime;
	}

	qcp->recordStartTime = _lastRecordEndTime;
	qcp->parameter = static_cast<double>(qcp->recordEndTime - qcp->recordStartTime);
	_qcBuffer->push_back(&_streamID, qcp);

	Core::Time t;
	sendMessages(t);
}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
void QcPluginAvailability::generateReport(const QcBuffer* buf) const {
	if ( buf->empty() ) {
		return;
	}

	std::vector<double> result = availability(buf);
	WaveformQuality *obj;

	SEISCOMP_DEBUG("%s: %s - availability: %f   gaps count: %f   overlaps count: %f",
	               _streamID.c_str(), _name.c_str(),
	               result[0], result[1], result[2]);

	obj = new WaveformQuality();
	obj->setWaveformID(getWaveformID(_streamID));
	obj->setCreatorID(_app->creatorID());
	obj->setCreated(Core::Time::UTC());
	obj->setStart(buf->startTime());
	obj->setEnd(buf->endTime());
	obj->setType("report");
	obj->setParameter(_parameterNames[0]);
	obj->setValue(result[0]);
	obj->setLowerUncertainty(0.0);
	obj->setUpperUncertainty(0.0);
	obj->setWindowLength(static_cast<double>(buf->length()));
	pushObject(Object::Cast(obj));

	obj = new WaveformQuality();
	obj->setWaveformID(getWaveformID(_streamID));
	obj->setCreatorID(_app->creatorID());
	obj->setCreated(Core::Time::UTC());
	obj->setStart(buf->startTime());
	obj->setEnd(buf->endTime());
	obj->setType("report");
	obj->setParameter(_parameterNames[1]);
	obj->setValue(result[1]);
	obj->setLowerUncertainty(0.0);
	obj->setUpperUncertainty(0.0);
	obj->setWindowLength(static_cast<double>(buf->length()));
	pushObject(Object::Cast(obj));

	obj = new WaveformQuality();
	obj->setWaveformID(getWaveformID(_streamID));
	obj->setCreatorID(_app->creatorID());
	obj->setCreated(Core::Time::UTC());
	obj->setStart(buf->startTime());
	obj->setEnd(buf->endTime());
	obj->setType("report");
	obj->setParameter(_parameterNames[2]);
	obj->setValue(result[2]);
	obj->setLowerUncertainty(0.0);
	obj->setUpperUncertainty(0.0);
	obj->setWindowLength(static_cast<double>(buf->length()));
	pushObject(Object::Cast(obj));

}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
void QcPluginAvailability::generateAlert(const QcBuffer *, const QcBuffer *) const {
	// NOOP
	return;
}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
std::vector<double> QcPluginAvailability::availability(const QcBuffer *buf) const {
	std::vector<double> returnVector(3);
	returnVector[0] = 0.0; // availability
	returnVector[1] = 0.0; // gap count
	returnVector[2] = 0.0; // overlap count

	if ( buf->empty() ) {
		return returnVector;
	}

	int effectiveSamples = 0;
	Core::TimeWindow tw(buf->startTime(), buf->endTime());
	double samplingFrequency =  buf->front()->recordSamplingFrequency;

	if ( samplingFrequency == -1.0 ) {
		return returnVector; // a timeout entry
	}

	int estimatedSamples = Private::round(static_cast<double>(tw.length()) * samplingFrequency);
	int gapCount = 0;
	int overlapCount = 0;
	Core::Time lastTime = Core::Time();

	for ( auto it = buf->begin(); it != buf->end(); ++it ) {
		QcParameterCPtr qcp = (*it);

		double recordSamplingFrequency =  qcp->recordSamplingFrequency;
		if ( recordSamplingFrequency == -1.0 ) {
			continue; // a timeout entry
		}

		Core::TimeWindow tw2(qcp->recordStartTime, qcp->recordEndTime);
		int sampleCount = Private::round(static_cast<double>(tw2.length()) * recordSamplingFrequency);

		//! get gaps/overlaps
		if ( lastTime != Core::Time() ) {
			double diff = (double)(qcp->recordStartTime - lastTime);
			if ( diff > (0.5 / recordSamplingFrequency) ) {
				gapCount++;
			}
			if ( diff < (-0.5 / recordSamplingFrequency) ) {
				overlapCount++;
			}
		}
		lastTime = qcp->recordEndTime;

		//! get availability
		// record complete inside timeWindow
		if ( tw.contains(tw2) ) {
			effectiveSamples += sampleCount;
			continue;
		}

		// timeWindow complete inside record
		if ( tw2.contains(tw) ) {
			effectiveSamples = estimatedSamples;
			break;
		}

		// record at least overlaps timeWindow
		if ( tw.overlaps(tw2) ) {
			// cut record's extra data at the beginning
			double dt = static_cast<double>(tw.startTime() - qcp->recordStartTime);
			if ( dt > 0 ) {
				effectiveSamples += sampleCount - Private::round(dt * recordSamplingFrequency);
				continue;
			}
			// cut record's extra data at the end
			dt = static_cast<double>(qcp->recordEndTime - tw.endTime());
			if (dt > 0) {
				effectiveSamples += sampleCount - Private::round(dt * recordSamplingFrequency);
				continue;
			}
		}
	}

	returnVector[0] = 100.0 * effectiveSamples / estimatedSamples;
	// prevent availability > 100%
	if ( returnVector[0] > 100.0 ) {
		returnVector[0] = 100.0;
	}

	returnVector[1] = gapCount;
	returnVector[2] = overlapCount;
	return returnVector;
}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<




// >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
}
}
}
// <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
